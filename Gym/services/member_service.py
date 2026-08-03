# Gym/services/member_service.py
"""
member_service.py
------------------
Single source of truth for building Member (Enrollment) querysets.

Every place in the app that needs "a list of members" — the Member
Management Center, dashboard cards, billing, attendance, reports,
trainer views, renewal reminders, CRM features — should go through
`get_member_queryset()` instead of writing its own `Enrollment.objects.filter(...)`.

Design goals:
  - One gym-scoping choke point (no cross-gym leakage possible).
  - One definition of what "active" / "expired" / "pending" / etc. mean,
    so different pages can never silently disagree with each other.
  - Cheap to extend: new filters/sorts/search fields get added here once
    and every caller gets them for free.
  - Views stay dumb: they just forward request.GET into this function.

Nothing in this module touches HttpRequest/HttpResponse — it's pure
queryset-building so it can be unit tested and reused outside views
(management commands, celery tasks, the renewal-reminder cron, etc.).

Verified reverse relations used below (do not change without re-checking
the source models):
  - Attendence.user -> User has no related_name, so the reverse lookup
    from Enrollment is `user__attendence__...` (lowercased model name,
    matches AuthFit.models.Attendence's own spelling).
  - billing.Payment.enrollment -> Enrollment has related_name='payments',
    so the reverse lookup/prefetch is `payments`.
"""

from __future__ import annotations

from typing import Optional

from django.db.models import (
    Q, F, Case, When, Value, CharField, Prefetch, QuerySet,
)
from django.utils import timezone

from AuthFit.models import Enrollment, MembershipPlanChangeLog


# ──────────────────────────────────────────────────────────────────────────
# Public constants — reused by views, forms, and templates so the list of
# valid filters/sorts is defined exactly once.
# ──────────────────────────────────────────────────────────────────────────

FILTER_CHOICES = [
    ("all",              "All Members"),
    ("active",           "Active Members"),
    ("unregistered",     "Unregistered Members"),   # NEW
    ("new_month",        "New This Month"),
    ("pending",          "Pending Payments"),
    ("expiring_today",   "Expiring Today"),
    ("attendance_today", "Today's Attendance"),
    ("renewals_today",   "Renewals Today"),
    ("expired",          "Expired Members"),
]
_VALID_FILTERS = {key for key, _ in FILTER_CHOICES}

SORT_CHOICES = [
    ("newest",       "Newest"),
    ("oldest",       "Oldest"),
    ("name",         "Name"),
    ("expiry_date",  "Expiry Date"),
    ("last_payment", "Last Payment"),
]
_VALID_SORTS = {key for key, _ in SORT_CHOICES}

_SORT_MAP = {
    "newest":       ("-created_at",),
    "oldest":       ("created_at",),
    "name":         ("fullname",),
    "expiry_date":  ("DueDate",),
    "last_payment": ("-paymentDate",),
}
_DEFAULT_SORT = "newest"

SEARCH_FIELDS = ("fullname", "phone", "unique_id")

MEMBERSHIP_STATUS_CHOICES = [
    ("active",  "Active"),
    ("pending", "Pending Payment"),
    ("expired", "Expired"),
]


# ──────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────

def _base_queryset(gym) -> QuerySet:
    """
    The one place gym-scoping happens. Every queryset this service returns
    is derived from this call, so cross-gym leakage is structurally hard.

    Soft-deleted enrollments are excluded everywhere by default — nothing
    in the Member Management Center's filter list is meant to surface
    deleted rows. Note: not is_active — that field means something
    different (transferred-out membership), not "hidden from lists".
    """
    return (
        Enrollment.objects
        .filter(gym=gym, is_deleted=False)
        .select_related("selectPlan", "trainer", "user")
    )


def _annotate_status(qs: QuerySet, today) -> QuerySet:
    """
    Annotate a computed membership status onto each row so templates and
    stat widgets don't need to re-derive it per-row in Python (which is
    what several existing views do today with `.is_expired` in a loop).

    Values: 'expired' | 'pending' | 'active'
    """
    return qs.annotate(
        computed_status=Case(
            When(DueDate__lt=today, then=Value("expired")),
            When(paymentStatus="Pending", then=Value("pending")),
            default=Value("active"),
            output_field=CharField(),
        )
    )

def get_member_whatsapp_log(enrollment, limit_per_type: int = 2) -> list:
    """
    WhatsApp reminder history for this member — last `limit_per_type`
    rows for EACH template type (before/after expiry), not a flat
    newest-N across both. Two slow renewals apart shouldn't push the
    "before" reminder off the list just because "after" reminders are
    more frequent, or vice versa.
    """
    from Gym.models import WhatsAppMessageLog

    qs = WhatsAppMessageLog.objects.filter(gym=enrollment.gym, member=enrollment)

    template_names = qs.values_list('template_name', flat=True).distinct()
    rows = []
    for name in template_names:
        rows.extend(qs.filter(template_name=name).order_by('-created_at')[:limit_per_type])

    rows.sort(key=lambda r: r.created_at, reverse=True)
    return rows

def _apply_filter(qs: QuerySet, filter_key: Optional[str], today) -> QuerySet:
    """Applies exactly one of the dashboard-card filters. `None`/'all' = no-op."""
    if not filter_key or filter_key == "all":
        return qs

    if filter_key not in _VALID_FILTERS:
        # Unknown filter — fail safe to "all" rather than raising, so a
        # bad querystring never 500s a dashboard card.
        return qs

    month_start = today.replace(day=1)

    if filter_key == "active":
        return qs.filter(DueDate__gte=today)

    if filter_key == "unregistered":     # NEW — Quick Enrollment members
        return qs.filter(user__isnull=True)

    if filter_key == "new_month":
        return qs.filter(doj__gte=month_start, doj__lte=today)

    if filter_key == "pending":
        return qs.filter(paymentStatus="Pending")

    if filter_key == "expiring_today":
        return qs.filter(DueDate=today)

    if filter_key == "expired":
        return qs.filter(DueDate__lt=today)

    if filter_key == "renewals_today":
        return _renewals_today_queryset(qs, today)

    if filter_key == "attendance_today":
       return qs.filter(attendance_logs__date=today).distinct()

    return qs



def _apply_search(qs: QuerySet, search: Optional[str]) -> QuerySet:
    """
    Instant search across Member Name, Mobile Number, and Unique ID —
    the three fields the spec calls out. icontains on all three, OR'd.
    """
    search = (search or "").strip()
    if not search:
        return qs
    q = Q()
    for field in SEARCH_FIELDS:
        q |= Q(**{f"{field}__icontains": search})
    return qs.filter(q)


def _apply_extra_filters(qs: QuerySet, **filters) -> QuerySet:
    """
    Optional filters beyond the dashboard cards — plan, trainer, gender,
    payment_status, membership_status, joining_month, expiry_month, batch.
    All are no-ops when not passed, so existing callers are unaffected.
    `membership_status` reads the `computed_status` annotation, so this
    must run after `_annotate_status`.
    """
    plan = filters.get("plan")
    if plan:
        qs = qs.filter(selectPlan_id=plan)

    trainer = filters.get("trainer")
    if trainer:
        qs = qs.filter(trainer_id=trainer)

    gender = filters.get("gender")
    if gender:
        qs = qs.filter(gender=gender)

    payment_status = filters.get("payment_status")
    if payment_status:
        qs = qs.filter(paymentStatus=payment_status)

    membership_status = filters.get("membership_status")
    if membership_status:
        qs = qs.filter(computed_status=membership_status)

    joining_month = filters.get("joining_month")  # expects "YYYY-MM"
    if joining_month:
        try:
            year, month = (int(x) for x in joining_month.split("-"))
            qs = qs.filter(doj__year=year, doj__month=month)
        except (ValueError, AttributeError):
            pass

    expiry_month = filters.get("expiry_month")  # expects "YYYY-MM"
    if expiry_month:
        try:
            year, month = (int(x) for x in expiry_month.split("-"))
            qs = qs.filter(DueDate__year=year, DueDate__month=month)
        except (ValueError, AttributeError):
            pass

    # `batch` is future-ready per the spec (Batch Timing doesn't exist as
    # a queryable field on Enrollment yet) — accepted and silently
    # no-op'd until that field/model exists, so callers can wire the UI
    # control now without breaking. Same for an `attendance_status`
    # (present/absent, as distinct from the `attendance_today` dashboard
    # filter) kwarg — intentionally not accepted yet; add it here once
    # there's a real field/query to back it, rather than faking it.
    return qs


def _apply_sort(qs: QuerySet, sort_key: Optional[str]) -> QuerySet:
    order_fields = _SORT_MAP.get(sort_key, _SORT_MAP[_DEFAULT_SORT])
    return qs.order_by(*order_fields)

def _renewals_today_queryset(qs: QuerySet, today) -> QuerySet:
    """
    A renewal = a payment was made today AND at least one earlier
    payment exists for that same enrollment. A brand-new member's
    first-ever payment (also dated today) has no earlier payment,
    so they are correctly excluded here.
    """
    from billing.models import Payment
    from django.db.models import Exists, OuterRef

    paid_today = Payment.objects.filter(
        enrollment=OuterRef('pk'), payment_date=today
    )
    paid_before = Payment.objects.filter(
        enrollment=OuterRef('pk'), payment_date__lt=today
    )
    return qs.filter(Exists(paid_today), Exists(paid_before))

def _apply_performance_hints(qs: QuerySet) -> QuerySet:
    """
    prefetch_related for anything list/detail views will commonly need
    alongside the base row, kept here so every caller gets the same
    N+1-safe queryset without repeating the prefetch wiring.

    `"payments"` is the verified related_name from billing.Payment.enrollment.
    Ordered by -payment_date and sliced to 1 so `latest_payment_list[0]`
    gives "Last Payment" without a second query per row.

    Kept intentionally light for the *list* case — heavier prefetches
    (full invoice history, all attendance) belong in a detail-specific
    variant, not the shared list queryset.
    """
    from billing.models import Payment  # local import avoids a hard
                                         # circular import at module load

    return qs.prefetch_related(
        Prefetch(
            "payments",
            queryset=Payment.objects.order_by("-payment_date")[:1],
            to_attr="latest_payment_list",
        ),
    )


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def get_member_queryset(
    gym,
    *,
    filter: Optional[str] = None,          # noqa: A002 — matches spec's param name
    search: Optional[str] = None,
    sort: Optional[str] = None,
    plan: Optional[str] = None,
    trainer: Optional[str] = None,
    gender: Optional[str] = None,
    payment_status: Optional[str] = None,
    membership_status: Optional[str] = None,
    batch: Optional[str] = None,
    with_performance_hints: bool = True,
) -> QuerySet:
    """
    Build a gym-scoped, filtered, searched, sorted Enrollment queryset.

    This is the ONLY function callers should use to list members. Views
    should do nothing more than pull params off `request.GET` and pass
    them straight through — see `get_member_queryset_from_request`.

    Args:
        gym: the tenant Gym instance (always required — no gym-less mode;
             this service is not for the super-admin cross-gym views).
        filter: one of FILTER_CHOICES keys (dashboard-card filters).
        search: free-text, matched against name / phone / unique_id.
        sort: one of SORT_CHOICES keys.
        plan, trainer, gender, payment_status, membership_status,
        joining_month, expiry_month, batch: optional narrowing filters,
             all additive to `filter` (e.g. filter='pending' AND
             trainer=<id> both apply).
        with_performance_hints: set False if a caller wants the bare
             filtered/sorted queryset and intends to attach its own
             prefetch/annotate (e.g. a detail view needing much more).

    Returns:
        QuerySet[Enrollment], not yet evaluated.
    """
    if gym is None:
        # Never silently return cross-gym or ungated data.
        return Enrollment.objects.none()

    today = timezone.localdate()

    qs = _base_queryset(gym)
    qs = _annotate_status(qs, today)
    qs = _apply_filter(qs, filter, today)
    qs = _apply_search(qs, search)
    qs = _apply_extra_filters(
        qs,
        plan=plan,
        trainer=trainer,
        gender=gender,
        payment_status=payment_status,
        membership_status=membership_status,
        batch=batch,
    )
    qs = _apply_sort(qs, sort)

    if with_performance_hints:
        qs = _apply_performance_hints(qs)

    return qs


def get_member_stats(
    gym,
    *,
    search: Optional[str] = None,
    plan: Optional[str] = None,
    trainer: Optional[str] = None,
    gender: Optional[str] = None,
    payment_status: Optional[str] = None,
    batch: Optional[str] = None,
) -> dict:
    """
    Counts for the Member List's statistics row: Total / Active / Expired /
    Pending Payments / Today's Attendance. Honors search + the narrowing
    filters (plan, trainer, gender, ...) but deliberately ignores whatever
    single `filter=` the page is currently showing — the stats row shows
    all five numbers side by side regardless of which card the user is on.

    Built entirely on top of `get_member_queryset()` so there is exactly
    one definition of what "active"/"expired"/"pending" mean, shared with
    the list itself. Five small `.count()` queries rather than one
    aggregate — simple and correct; revisit with a single annotated
    aggregate query if this ever shows up in profiling.
    """
    common = dict(
        gym=gym, search=search, plan=plan, trainer=trainer, gender=gender,
        payment_status=payment_status, batch=batch, with_performance_hints=False,
    )
    return {
        "total":            get_member_queryset(filter="all", **common).count(),
        "active":           get_member_queryset(filter="active", **common).count(),
        "unregistered":     get_member_queryset(filter="unregistered", **common).count(),  # NEW
        "expired":          get_member_queryset(filter="expired", **common).count(),
        "pending":          get_member_queryset(filter="pending", **common).count(),
        "today_attendance": get_member_queryset(filter="attendance_today", **common).count(),
    }


def get_member_detail_queryset(gym) -> QuerySet:
    from billing.models import Payment, Invoice

    qs = get_member_queryset(gym=gym, with_performance_hints=False)
    return qs.prefetch_related(
        Prefetch("payments", queryset=Payment.objects.order_by("-payment_date", "-id")),
        Prefetch(
            "invoices",
            queryset=Invoice.objects.filter(status__in=Invoice.REVENUE_STATUSES)
                                     .order_by("-invoice_date", "-invoice_number"),
        ),
        Prefetch(
            "plan_change_logs",
            queryset=MembershipPlanChangeLog.objects
                .select_related("old_plan", "new_plan", "changed_by")
                .order_by("-created_at"),
        ),
    )


def get_member_queryset_from_request(request, **overrides) -> QuerySet:
    """
    Convenience wrapper for the common case: a view just wants to pull
    filter/search/sort/plan/trainer/... straight off request.GET for
    request.gym. `overrides` lets a caller pin specific params (e.g. a
    dashboard card that always wants filter='pending' regardless of
    querystring) while still reading the rest from the request.
    """
    params = {
        "filter": request.GET.get("filter"),
        "search": request.GET.get("search"),
        "sort": request.GET.get("sort"),
        "plan": request.GET.get("plan"),
        "trainer": request.GET.get("trainer"),
        "gender": request.GET.get("gender"),
        "payment_status": request.GET.get("payment_status"),
        "membership_status": request.GET.get("membership_status"),
        "batch": request.GET.get("batch"),
    }
    params.update(overrides)
    return get_member_queryset(gym=getattr(request, "gym", None), **params)

def get_member_financial_summary(enrollment) -> dict:
    """
    Financial Summary cards for the Member Detail page. Reads from
    enrollment.payments.all() — when called on a row from
    get_member_detail_queryset() this hits Django's prefetch cache (no
    extra query); called standalone it just runs a normal query.
    """
    payments = list(enrollment.payments.all())
    payments.sort(key=lambda p: p.payment_date, reverse=True)

    lifetime_revenue = sum((p.paid_amount for p in payments), start=type(enrollment.pendingAmount)(0))
    last_payment = payments[0] if payments else None

    return {
        "lifetime_revenue": lifetime_revenue,
        "outstanding_amount": enrollment.pendingAmount,
        "total_paid": enrollment.paidAmount,
        "last_payment": last_payment,
        "next_due": enrollment.DueDate,
    }


def get_member_attendance_summary(enrollment) -> dict:
    from django.utils import timezone

    empty = {
        "today_marked": False, "last_attendance": None, "attendance_pct": None,
        "present_days": 0, "absent_days": 0, "records": [],
    }

    today = timezone.localdate()
    join_date = enrollment.membership_start_date or enrollment.doj
    records = list(enrollment.attendance_logs.order_by("-date"))
    if not records:
        return empty

    total_days_enrolled = max((today - join_date).days + 1, 1)
    present_days = len(records)
    absent_days = max(total_days_enrolled - present_days, 0)
    attendance_pct = round((present_days / total_days_enrolled) * 100, 1)

    return {
        "today_marked": records[0].date == today,
        "last_attendance": records[0].date,
        "attendance_pct": attendance_pct,
        "present_days": present_days,
        "absent_days": absent_days,
        "records": records[:30],
    }


def get_member_activity_timeline(enrollment, limit: int = 7) -> list:
    TYPE_RANK = {
        "joined": 0,
        "plan_change": 1,
        "payment": 2,
        "invoice": 3,
    }

    events = []

    events.append({
        "type": "joined",
        "label": "Joined Gym",
        "date": enrollment.doj,
        "detail": f"Enrolled with {enrollment.selectPlan.plan}" if enrollment.selectPlan else "Enrolled",
    })

    for log in enrollment.plan_change_logs.all():
        old_name = log.old_plan.plan if log.old_plan else "—"
        new_name = log.new_plan.plan if log.new_plan else "—"
        events.append({
            "type": "plan_change",
            "label": "Plan Changed",
            "date": log.created_at.date(),
            "detail": f"{old_name} → {new_name}" + (f" ({log.reason})" if log.reason else ""),
        })

    for payment in enrollment.payments.all():
        events.append({
            "type": "payment",
            "label": "Payment Received",
            "date": payment.payment_date,
            "detail": f"₹{payment.paid_amount} via {payment.get_payment_method_display() if payment.payment_method else 'Unknown'}",
        })

    for invoice in enrollment.invoices.all():
        events.append({
            "type": "invoice",
            "label": "Invoice Generated",
            "date": invoice.invoice_date,
            "detail": f"{invoice.invoice_number} — ₹{invoice.grand_total}",
        })
    events.sort(key=lambda e: (e["date"], TYPE_RANK.get(e["type"], 0)), reverse=True)
    return events[:limit]

def get_member_push_log(enrollment, limit_per_channel: int = 2) -> list:
    """
    FCM + Web Push history for this member — last `limit_per_channel`
    rows for EACH channel, so one noisy channel can't crowd out the
    other in the member-detail view.
    """
    from Gym.models import PushNotificationLog

    qs = PushNotificationLog.objects.filter(gym=enrollment.gym, member=enrollment)

    rows = []
    for channel, _ in PushNotificationLog.CHANNEL_CHOICES:
        rows.extend(qs.filter(channel=channel).order_by('-created_at')[:limit_per_channel])

    rows.sort(key=lambda r: r.created_at, reverse=True)
    return rows
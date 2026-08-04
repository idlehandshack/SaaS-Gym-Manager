# services/platform_insights.py
from __future__ import annotations
import hashlib
import subprocess
import time
from datetime import timedelta
from typing import Any
from django.db.models import OuterRef, Subquery
from django.core.cache import cache
from django.db.models import Avg, Count, DecimalField, Q, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.utils import timezone

from Gym.models import Gym, SubscriptionPlan

CACHE_TTL = 60 * 5  # 5 minutes
CACHE_PREFIX = "platform_insights"
DASHBOARD_CACHE_TTL = 60 * 5  # 5 minutes
NOTIFICATIONS_CACHE_TTL = 8   # 5-10s per spec
_FIELD_CACHE: dict[tuple[str, str], bool] = {}


def _has_field(model, field_name: str) -> bool:
    key = (model.__name__, field_name)
    if key not in _FIELD_CACHE:
        try:
            model._meta.get_field(field_name)
            _FIELD_CACHE[key] = True
        except Exception:
            _FIELD_CACHE[key] = False
    return _FIELD_CACHE[key]

def _get_subscription_payment_model():
    try:
        from Gym.models import SubscriptionPayment
        return SubscriptionPayment
    except ImportError:
        return None
    
def _get_enrollment_model():
    from AuthFit.models import Enrollment
    return Enrollment


def _safe(builder, default):
    try:
        return builder()
    except Exception:
        return default
def _cache_key(name: str, filters: dict[str, Any] | None = None) -> str:
    if not filters:
        return f"{CACHE_PREFIX}:{name}"
    raw = "&".join(
        f"{k}={v}"
        for k, v in sorted(filters.items())
        if v not in (None, "", [])
    )
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return f"{CACHE_PREFIX}:{name}:{digest}"


def invalidate_platform_insights_cache() -> None:
    try:
        cache.incr(f"{CACHE_PREFIX}:version")
    except ValueError:
        cache.set(f"{CACHE_PREFIX}:version", 1, timeout=None)


def _versioned_key(name: str, filters: dict[str, Any] | None = None) -> str:
    version = cache.get(f"{CACHE_PREFIX}:version", 1)
    return f"{_cache_key(name, filters)}:v{version}"


def get_cached(name: str, builder, filters: dict[str, Any] | None = None):
    key = _versioned_key(name, filters)
    data = cache.get(key)
    if data is None:
        data = builder()
        cache.set(key, data, CACHE_TTL)
    return data

def _revenue_subquery():
    from AuthFit.models import Enrollment
    return (
        Enrollment.objects
        .filter(gym=OuterRef("pk"), is_deleted=False)
        .values("gym")
        .annotate(total=Sum("Amount"))
        .values("total")
    )
def _base_gym_qs():
    return (
        Gym.objects
        .select_related("plan", "owner")
        .annotate(
            member_count=Count("enrollment", distinct=True),
            trainer_count=Count(
                "staff",
                filter=Q(staff__role="trainer", staff__active=True),
                distinct=True,
            ),
            revenue=Coalesce(
                Subquery(_revenue_subquery(), output_field=DecimalField(max_digits=12, decimal_places=2)),
                0,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )
    )


def apply_filters(qs, filters, apply_date_range=True):
    today = timezone.now().date()

    plan = filters.get("plan")
    if plan:
        qs = qs.filter(plan__name=plan)

    status = filters.get("status")
    if status == "active":
        qs = qs.filter(active=True, subscription_end__gte=today)
    elif status == "trial":
        if _has_field(Gym, "is_trial"):
            qs = qs.filter(is_trial=True)
        else:
            qs = qs.none()
    elif status == "expired":
        qs = qs.filter(Q(active=False) | Q(subscription_end__lt=today))
    elif status == "inactive":
        qs = qs.filter(active=False)

    state = filters.get("state")
    if state:
        qs = qs.filter(state__iexact=state)

    city = filters.get("city")
    if city:
        qs = qs.filter(city__iexact=city)

    search = filters.get("search")
    if search:
        qs = qs.filter(
            Q(gym_name__icontains=search)
            | Q(gym_code__icontains=search)
            | Q(owner__username__icontains=search)
        )

    date_from, date_to = _resolve_date_range(filters)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if apply_date_range:
            date_from, date_to = _resolve_date_range(filters)
            if date_from:
                qs = qs.filter(created_at__date__gte=date_from)
            if date_to:
                qs = qs.filter(created_at__date__lte=date_to)
    return qs


def _resolve_date_range(filters: dict[str, Any]):
    """Translate the 'range' filter key into a (start, end) date tuple."""
    today = timezone.now().date()
    range_key = filters.get("range", "")
    if range_key == "all":
        return None, None
    if range_key == "today":
        return today, today
    if range_key == "7d":
        return today - timedelta(days=7), today
    if range_key == "30d":
        return today - timedelta(days=30), today
    if range_key == "90d":
        return today - timedelta(days=90), today
    if range_key == "year":
        return today.replace(month=1, day=1), today
    if range_key == "custom":
        return filters.get("date_from"), filters.get("date_to")
    return None, None

def get_kpi_summary(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        today = timezone.now().date()
        month_start = today.replace(day=1)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
        prev_month_end = month_start - timedelta(days=1)

        gyms = apply_filters(_base_gym_qs(), filters)

        total_gyms = gyms.count()
        active_gyms = gyms.filter(active=True, subscription_end__gte=today).count()

        capacity = gyms.aggregate(total_members=Coalesce(Sum("member_count"), 0))
        total_members = capacity["total_members"]

        # ── Revenue now comes from SubscriptionPayment, scoped to the selected RANGE filter ──
        SubscriptionPayment = _get_subscription_payment_model()
        date_from, date_to = _resolve_date_range(filters)
        effective_end = date_to or today

        if SubscriptionPayment is not None:
            revenue_qs = SubscriptionPayment.objects.filter(gym__in=gyms)
            if date_from:
                revenue_qs = revenue_qs.filter(payment_date__gte=date_from)
            revenue_qs = revenue_qs.filter(payment_date__lte=effective_end)
            monthly_revenue = revenue_qs.aggregate(
                total=Coalesce(
                    Sum("amount_paid"), 0,
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )["total"] or 0

            # Previous period of the same length, for the growth-trend comparison
            if date_from:
                period_len = (effective_end - date_from).days
                prev_end = date_from - timedelta(days=1)
                prev_start = prev_end - timedelta(days=period_len)
                prev_monthly_revenue = SubscriptionPayment.objects.filter(
                    gym__in=gyms, payment_date__gte=prev_start, payment_date__lte=prev_end,
                ).aggregate(
                    total=Coalesce(
                        Sum("amount_paid"), 0,
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    )
                )["total"] or 0
            else:
                prev_monthly_revenue = 0  # "All Time" has no meaningful "previous period"
        else:
            monthly_revenue = 0
            prev_monthly_revenue = 0

        # ── Renewal rate — driven by SubscriptionPayment (the Renew button flow) ──
        if SubscriptionPayment is not None:
            renewed_this_month = SubscriptionPayment.objects.filter(
                gym__in=gyms, payment_date__gte=month_start,
            ).values("gym").distinct().count()
            eligible_for_renewal = gyms.filter(
                subscription_end__lte=today, subscription_end__gte=prev_month_start,
            ).count()
            renewal_rate = (
                round(renewed_this_month / eligible_for_renewal * 100, 1)
                if eligible_for_renewal else 0.0
            )
        else:
            renewal_rate = 0.0

        # ── Pending subscription payments (gyms marked "No" on renew) ──
        pending_agg = gyms.aggregate(
            pending_total=Coalesce(
                Sum("pending_amount"), 0,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            pending_gyms=Count("id", filter=Q(pending_amount__gt=0), distinct=True),
        )

        gyms_this_month = gyms.filter(created_at__date__gte=month_start).count()
        gyms_prev_month = gyms.filter(
            created_at__date__gte=prev_month_start,
            created_at__date__lte=prev_month_end,
        ).count()
        growth_pct = (
            round((gyms_this_month - gyms_prev_month) / gyms_prev_month * 100, 1)
            if gyms_prev_month
            else (100.0 if gyms_this_month else 0.0)
        )
        revenue_growth_pct = (
            round((float(monthly_revenue) - float(prev_monthly_revenue)) / float(prev_monthly_revenue) * 100, 1)
            if prev_monthly_revenue
            else (100.0 if monthly_revenue else 0.0)
        )

        return {
            "total_gyms": total_gyms,
            "active_gyms": active_gyms,
            "total_members": total_members,
            "monthly_revenue": float(monthly_revenue),
            "renewal_rate": renewal_rate,
            "growth_pct": growth_pct,
            "revenue_growth_pct": revenue_growth_pct,
            "gyms_this_month": gyms_this_month,
            "gyms_prev_month": gyms_prev_month,
            "pending_payment_total": float(pending_agg["pending_total"] or 0),
            "pending_payment_gyms": pending_agg["pending_gyms"],
        }

    return get_cached("kpi_summary", build, filters)

def _get_service_status(service_name: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True, text=True, timeout=2,
        )
        state = result.stdout.strip()
        if state == "active":
            return "Running"
        if state in ("inactive", "dead"):
            return "Stopped"
        return "Failed"
    except Exception:
        return "Failed"


def _get_cpu_usage():
    try:
        import psutil
        return round(psutil.cpu_percent(interval=1), 1)
    except Exception:
        return None


def _get_memory_usage():
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {
            "usage": round(vm.percent),
            "used": f"{vm.used / (1024**3):.1f} GB",
            "total": f"{vm.total / (1024**3):.1f} GB",
        }
    except Exception:
        return None


def _get_disk_usage():
    try:
        import psutil
        du = psutil.disk_usage("/")
        return {
            "usage": round(du.percent),
            "used": f"{du.used / (1024**3):.0f} GB",
            "total": f"{du.total / (1024**3):.0f} GB",
        }
    except Exception:
        return None


def _get_uptime():
    try:
        import psutil
        delta = timezone.now().timestamp() - psutil.boot_time()
        days = int(delta // 86400)
        hours = int((delta % 86400) // 3600)
        minutes = int((delta % 3600) // 60)
        return {"days": days, "hours": hours, "minutes": minutes}
    except Exception:
        return None


def _get_cron_status():
    last_run = cache.get("cron:last_run")
    expected_interval_minutes = 60  # match your actual cron interval

    if last_run is None:
        return {"status": "Warning", "last_run": None, "progress": 60}

    elapsed_minutes = (timezone.now() - last_run).total_seconds() / 60
    if elapsed_minutes <= expected_interval_minutes:
        return {"status": "Running", "last_run": last_run.isoformat(), "progress": 100}
    if elapsed_minutes <= expected_interval_minutes * 2:
        return {"status": "Warning", "last_run": last_run.isoformat(), "progress": 60}
    return {"status": "Failed", "last_run": last_run.isoformat(), "progress": 0}


def _get_web_push_status():
    try:
        from django.conf import settings
        webpush_settings = getattr(settings, "WEBPUSH_SETTINGS", {}) or {}
        vapid_public = getattr(settings, "VAPID_PUBLIC_KEY", None) or webpush_settings.get("VAPID_PUBLIC_KEY")
        vapid_private = getattr(settings, "VAPID_PRIVATE_KEY", None) or webpush_settings.get("VAPID_PRIVATE_KEY")

        fcm_ready = False
        try:
            import firebase_admin
            fcm_ready = bool(firebase_admin._apps)
        except Exception:
            fcm_ready = False

        if vapid_public and vapid_private and fcm_ready:
            return "Operational"
        if vapid_public and vapid_private:
            return "Configuration Error"
        return "Unavailable"
    except Exception:
        return "Unavailable"
    
def get_system_health() -> dict[str, Any]:
    def build():
        from django.db import connection

        # Database
        db_ok = True
        try:
            connection.ensure_connection()
        except Exception:
            db_ok = False
        database = {"status": "Operational" if db_ok else "Down", "progress": 100 if db_ok else 0}
        redis_status = "Operational"
        try:
            probe_key = f"_health_check_{int(time.time() * 1000)}"
            start = time.monotonic()
            cache.set(probe_key, "1", timeout=5)
            val = cache.get(probe_key)
            cache.delete(probe_key)
            elapsed = time.monotonic() - start
            if val != "1" or elapsed > 0.5:
                redis_status = "Degraded"
        except Exception:
            redis_status = "Down"
        redis_progress = {"Operational": 100, "Degraded": 60, "Down": 0}[redis_status]

        cron = _get_cron_status()
        cpu_usage = _get_cpu_usage()
        memory = _get_memory_usage()
        disk = _get_disk_usage()

        web_push_status = _get_web_push_status()
        web_push_progress = {"Operational": 100, "Configuration Error": 40, "Unavailable": 0}[web_push_status]

        gunicorn_status = _get_service_status("gunicorn")
        nginx_status = _get_service_status("nginx")
        svc_progress = {"Running": 100, "Stopped": 0, "Failed": 0}
        uptime = _get_uptime()

        critical = (
            database["status"] == "Down"
            or redis_status == "Down"
            or gunicorn_status != "Running"
            or nginx_status != "Running"
        )
        warning = (
            redis_status == "Degraded"
            or cron["status"] in ("Warning", "Failed")
            or web_push_status != "Operational"
            or (cpu_usage is not None and cpu_usage > 80)
            or (memory and memory["usage"] > 90)
            or (disk and disk["usage"] > 90)
        )
        platform_status = "Critical" if critical else ("Warning" if warning else "Healthy")

        return {
            "database": database,
            "redis": {"status": redis_status, "progress": redis_progress},
            "cron": cron,
            "cpu": {"usage": cpu_usage},
            "memory": memory or {"usage": None, "used": None, "total": None},
            "disk": disk or {"usage": None, "used": None, "total": None},
            "web_push": {"status": web_push_status, "progress": web_push_progress},
            "gunicorn": {"status": gunicorn_status, "progress": svc_progress[gunicorn_status]},
            "nginx": {"status": nginx_status, "progress": svc_progress[nginx_status]},
            "uptime": uptime,
            "platform_status": platform_status,
            "checked_at": timezone.now().isoformat(),
        }
    key = f"{CACHE_PREFIX}:system_health"
    data = cache.get(key)
    if data is None:
        data = build()
        cache.set(key, data, 25)
    return data

def get_subscription_analytics(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        gyms = apply_filters(_base_gym_qs(), filters)
        SubscriptionPayment = _get_subscription_payment_model()

        plans = (
            SubscriptionPlan.objects.annotate(
                gym_count=Count("gym", filter=Q(gym__in=gyms), distinct=True),
                monthly_revenue=Coalesce(
                    Sum("gym__enrollment__Amount", filter=Q(gym__in=gyms)), 0,
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                pending_gym_count=Count(
                    "gym", filter=Q(gym__in=gyms, gym__pending_amount__gt=0), distinct=True,
                ),
                pending_amount_total=Coalesce(
                    Sum("gym__pending_amount", filter=Q(gym__in=gyms)), 0,
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
            .order_by("-gym_count")
        )

        plan_data = []
        most_popular = None
        max_count = -1
        for p in plans:
            renewed_count = 0
            if SubscriptionPayment is not None:
                renewed_count = (
                    SubscriptionPayment.objects
                    .filter(plan=p, gym__in=gyms)
                    .values("gym").distinct().count()
                )
            renewal_pct = (
                round(renewed_count / p.gym_count * 100, 1) if p.gym_count else 0.0
            )
            annual_revenue = float(p.monthly_revenue) * 12
            plan_data.append({
                "name": p.name,
                "gym_count": p.gym_count,
                "monthly_revenue": float(p.monthly_revenue),
                "annual_revenue": annual_revenue,
                "renewal_pct": renewal_pct,
                "pending_gym_count": p.pending_gym_count,
                "pending_amount_total": float(p.pending_amount_total or 0),
            })
            if p.gym_count > max_count:
                max_count = p.gym_count
                most_popular = p.name

        return {"plans": plan_data, "most_popular_plan": most_popular}

    return get_cached("subscription_analytics", build, filters)

def get_platform_activity(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        today = timezone.now().date()
        gym_ids = apply_filters(_base_gym_qs(), filters).values_list("id", flat=True)

        def safe_count(app_model_path, **kwargs):
            try:
                module_path, model_name = app_model_path.rsplit(".", 1)
                module = __import__(module_path, fromlist=[model_name])
                model = getattr(module, model_name)
                return model.objects.filter(**kwargs).count()
            except Exception:
                return 0

        SubscriptionPayment = _get_subscription_payment_model()
        if SubscriptionPayment is not None:
            renewals_today = SubscriptionPayment.objects.filter(
                gym_id__in=gym_ids, payment_date=today,
            ).count()
        else:
            renewals_today = 0

        return {
            "members_checked_in": safe_count(
                "AuthFit.models.AttendanceRecord", gym_id__in=gym_ids, timestamp__date=today,
            ),
            "attendance_records": safe_count(
                "AuthFit.models.AttendanceRecord", gym_id__in=gym_ids, timestamp__date=today,
            ),
            "invoices_generated": safe_count(
                "AuthFit.models.Invoice", gym_id__in=gym_ids, created_at__date=today,
            ),
            "payments_recorded": safe_count(
                "AuthFit.models.Payment", gym_id__in=gym_ids, created_at__date=today,
            ),
            "products_sold": safe_count(
                "AuthFit.models.ProductSale", gym_id__in=gym_ids, created_at__date=today,
            ),
            "new_registrations": safe_count(
                "AuthFit.models.Enrollment", gym_id__in=gym_ids, created_at__date=today,
            ),
            "renewals": renewals_today,
        }

    return get_cached("platform_activity", build, filters)

def get_engagement_analytics(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        today = timezone.now().date()
        week_start = today - timedelta(days=6)
        month_start = today.replace(day=1)

        gyms = apply_filters(_base_gym_qs(), filters)

        daily_active = gyms.filter(owner__last_login__date=today).count()
        weekly_active = gyms.filter(owner__last_login__date__gte=week_start).count()
        monthly_active = gyms.filter(owner__last_login__date__gte=month_start).count()

        total = gyms.count()

        return {
            "daily_active_gyms": daily_active,
            "weekly_active_gyms": weekly_active,
            "monthly_active_gyms": monthly_active,
            "avg_login_frequency": round(weekly_active / total, 2) if total else 0.0,
        }

    return get_cached("engagement_analytics", build, filters)

def get_renewal_churn(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        from AuthFit.models import Enrollment

        today = timezone.now().date()
        start = today - timedelta(days=29)
        gyms = apply_filters(_base_gym_qs(), filters)
        gym_ids = gyms.values_list("id", flat=True)
        enrollments = Enrollment.objects.filter(gym_id__in=gym_ids)
        has_status = _has_field(Enrollment, "status")

        expired_filter = Q(status="expired") if has_status else Q(pk__in=[])
        frozen_filter = Q(status="frozen") if has_status else Q(pk__in=[])
        cancelled_filter = Q(status="cancelled") if has_status else Q(pk__in=[])

        agg = enrollments.aggregate(
            expired=Count("id", filter=expired_filter, distinct=True),
            frozen=Count("id", filter=frozen_filter, distinct=True),
            cancelled=Count("id", filter=cancelled_filter, distinct=True),
            total=Count("id", distinct=True),
        )

        # ── Renewals now come from SubscriptionPayment (Renew button "Yes" flow) ──
        SubscriptionPayment = _get_subscription_payment_model()
        if SubscriptionPayment is not None:
            renewal_qs = SubscriptionPayment.objects.filter(gym_id__in=gym_ids)
            renewed = renewal_qs.values("gym").distinct().count()

            daily = (
                renewal_qs.filter(payment_date__gte=start)
                .values("payment_date")
                .annotate(count=Count("id", distinct=True))
                .order_by("payment_date")
            )
            daily_map = {row["payment_date"].isoformat(): row["count"] for row in daily}
        else:
            renewed = 0
            daily_map = {}

        # Denominator: gyms that were actually due (had a subscription end date in range)
        eligible = gyms.filter(subscription_end__isnull=False).count()
        renewal_rate = round(renewed / eligible * 100, 1) if eligible else 0.0
        churn_rate = (
            round((agg["expired"] + agg["cancelled"]) / agg["total"] * 100, 1)
            if agg["total"] else 0.0
        )

        labels = [(start + timedelta(days=i)).isoformat() for i in range(30)]
        series = [daily_map.get(d, 0) for d in labels]

        # ── Pending renewals (marked "No" on the Renew modal) ──
        pending_agg = gyms.aggregate(
            pending_total=Coalesce(
                Sum("pending_amount"), 0,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            pending_gyms=Count("id", filter=Q(pending_amount__gt=0), distinct=True),
        )

        return {
            "renewed": renewed,
            "expired": agg["expired"],
            "frozen": agg["frozen"],
            "cancelled": agg["cancelled"],
            "renewal_rate": renewal_rate,
            "churn_rate": churn_rate,
            "trend_labels": labels,
            "trend_series": series,
            "pending_renewal_gyms": pending_agg["pending_gyms"],
            "pending_renewal_total": float(pending_agg["pending_total"] or 0),
        }

    return get_cached("renewal_churn", build, filters)

def get_payment_analytics(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        gyms = apply_filters(_base_gym_qs(), filters)
        total_gyms = gyms.count()

        SubscriptionPayment = _get_subscription_payment_model()
        renewed_gym_ids = set()
        collected_total = 0.0
        if SubscriptionPayment is not None:
            payments = SubscriptionPayment.objects.filter(gym__in=gyms)
            renewed_gym_ids = set(payments.values_list("gym_id", flat=True))
            collected_total = float(
                payments.aggregate(
                    total=Coalesce(
                        Sum("amount_paid"), 0,
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                )["total"] or 0
            )

        pending_agg = gyms.aggregate(
            pending_gyms=Count("id", filter=Q(pending_amount__gt=0), distinct=True),
        )
        pending_gyms = pending_agg["pending_gyms"]
        successful_gyms = len(renewed_gym_ids)
        not_renewed_gyms = total_gyms - successful_gyms  # ← failure = everyone who hasn't renewed

        success_rate = round(successful_gyms / total_gyms * 100, 1) if total_gyms else 0.0
        failure_rate = round(not_renewed_gyms / total_gyms * 100, 1) if total_gyms else 0.0

        return {
            "successful": successful_gyms,
            "not_renewed": not_renewed_gyms,
            "pending": pending_gyms,
            "collected_amount": collected_total,
            "success_rate": success_rate,
            "failure_rate": failure_rate,
        }

    return get_cached("payment_analytics", build, filters)

def get_dashboard(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        kpi = _safe(lambda: get_kpi_summary(filters), {
            "total_gyms": 0, "active_gyms": 0, "total_members": 0,
            "monthly_revenue": 0.0, "renewal_rate": 0.0,
            "growth_pct": 0.0, "revenue_growth_pct": 0.0,
            "gyms_this_month": 0, "gyms_prev_month": 0,
        })

        subscription = _safe(lambda: get_subscription_analytics(filters), {
            "plans": [], "most_popular_plan": None,
        })

        activity = _safe(lambda: get_platform_activity(filters), {
            "members_checked_in": 0, "attendance_records": 0, "invoices_generated": 0,
            "payments_recorded": 0, "products_sold": 0, "new_registrations": 0, "renewals": 0,
        })

        engagement = _safe(lambda: get_engagement_analytics(filters), {
            "daily_active_gyms": 0, "weekly_active_gyms": 0, "monthly_active_gyms": 0,
            "avg_login_frequency": 0.0,
        })

        renewal_churn = _safe(lambda: get_renewal_churn(filters), {
            "renewed": 0, "expired": 0, "frozen": 0, "cancelled": 0,
            "renewal_rate": 0.0, "churn_rate": 0.0, "trend_labels": [], "trend_series": [],
        })

        payments = _safe(lambda: get_payment_analytics(filters), {
            "successful": 0, "not_renewed": 0, "pending": 0, "collected_amount": 0.0,
            "success_rate": 0.0, "failure_rate": 0.0,
        })

        return {
            "kpi_summary": kpi,
            "subscription_analytics": subscription,
            "platform_activity": activity,
            "engagement_analytics": engagement,
            "renewal_churn": renewal_churn,
            "payment_analytics": payments,
        }

    return get_cached("dashboard", build, filters)
def _notif(kind, severity, title, detail, timestamp=None):
    return {
        "type": kind,
        "severity": severity,  # "info" | "warning" | "critical"
        "title": title,
        "detail": detail,
        "timestamp": (timestamp or timezone.now()).isoformat(),
    }


def get_notifications() -> dict[str, Any]:
    def build():
        now = timezone.now()
        today = now.date()
        notifications = []
        health = _safe(get_system_health, {})

        def check_new_gyms():
            count = Gym.objects.filter(created_at__date=today).count()
            if count:
                notifications.append(_notif(
                    "new_gym", "info", f"{count} new gym{'s' if count != 1 else ''} today",
                    "New gyms registered on the platform today.",
                ))
        _safe(check_new_gyms, None)

        def check_expiring():
            count = Gym.objects.filter(active=True, subscription_end=today).count()
            if count:
                notifications.append(_notif(
                    "expiring_today", "warning",
                    f"{count} subscription{'s' if count != 1 else ''} expiring today",
                    "These gyms will lose access unless renewed.",
                ))
        _safe(check_expiring, None)

        def check_pending_renewals():
            # NEW: gyms where Super Admin clicked "No" on the Renew modal
            count = Gym.objects.filter(pending_amount__gt=0).count()
            if count:
                total = Gym.objects.filter(pending_amount__gt=0).aggregate(
                    total=Coalesce(
                        Sum("pending_amount"), 0,
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    )
                )["total"]
                notifications.append(_notif(
                    "pending_renewal", "warning",
                    f"{count} gym{'s' if count != 1 else ''} with pending renewal payment",
                    f"₹{float(total):,.2f} outstanding across gyms marked as unpaid on renewal.",
                ))
        _safe(check_pending_renewals, None)

        def check_failed_payments():
            try:
                from AuthFit.models import Payment
            except ImportError:
                return
            count = Payment.objects.filter(
                status="failed", created_at__gte=now - timedelta(hours=24),
            ).count()
            if count:
                notifications.append(_notif(
                    "failed_payment", "critical",
                    f"{count} failed payment{'s' if count != 1 else ''} in last 24h",
                    "Review the payments table for details.",
                ))
        _safe(check_failed_payments, None)

        def check_cron():
            cron = health.get("cron", {})
            if cron.get("status") == "Failed":
                notifications.append(_notif(
                    "cron_failed", "critical", "Cron scheduler failed",
                    "Scheduled jobs (reminders/notifications) have not run within the expected interval.",
                ))
            elif cron.get("status") == "Warning":
                notifications.append(_notif(
                    "cron_warning", "warning", "Cron scheduler delayed",
                    "The last scheduled run exceeded the expected interval.",
                ))
        _safe(check_cron, None)

        def check_web_push():
            if health.get("web_push", {}).get("status") == "Unavailable":
                notifications.append(_notif(
                    "web_push_down", "warning", "Web push unavailable",
                    "Push notification delivery is not currently configured or reachable.",
                ))
        _safe(check_web_push, None)

        def check_resource_pressure():
            cpu = health.get("cpu", {}).get("usage")
            disk = health.get("disk", {}).get("usage")
            if cpu is not None and cpu > 85:
                notifications.append(_notif(
                    "high_cpu", "warning", f"High CPU usage: {cpu}%",
                    "Server CPU utilization is elevated.",
                ))
            if disk is not None and disk > 90:
                notifications.append(_notif(
                    "low_disk", "critical", f"Low disk space: {disk}% used",
                    "Free up disk space or provision more storage soon.",
                ))
        _safe(check_resource_pressure, None)

        def check_services():
            if health.get("gunicorn", {}).get("status") != "Running":
                notifications.append(_notif(
                    "gunicorn_down", "critical", "Gunicorn is not running",
                    "The application server appears to be down.",
                ))
            if health.get("nginx", {}).get("status") != "Running":
                notifications.append(_notif(
                    "nginx_down", "critical", "Nginx is not running",
                    "The reverse proxy appears to be down.",
                ))
        _safe(check_services, None)

        severity_rank = {"critical": 0, "warning": 1, "info": 2}
        notifications.sort(key=lambda n: severity_rank.get(n["severity"], 3))

        return {
            "unread_count": len(notifications),
            "critical_count": sum(1 for n in notifications if n["severity"] == "critical"),
            "notifications": notifications,
        }

    key = f"{CACHE_PREFIX}:notifications"
    data = cache.get(key)
    if data is None:
        data = build()
        cache.set(key, data, NOTIFICATIONS_CACHE_TTL)
    return data
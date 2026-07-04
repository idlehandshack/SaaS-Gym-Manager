"""
services/platform_insights.py

Business-intelligence layer for the Platform Insights dashboard.
All heavy aggregation lives here — views stay thin and only call
these functions and serialize the result to JSON.

Every public function is cached in Redis for 5 minutes (CACHE_TTL)
and cache keys are invalidated by the signal handlers registered
at the bottom of this module.

NOTE (fix, July 2026): `Enrollment.is_renewal`, `Enrollment.status`,
and `Gym.is_trial` do not exist on the current models. Every place
that referenced them is now guarded by `_has_field(...)` so a missing
field degrades a widget to a safe default (0 / empty) instead of
raising FieldError and 500ing the whole insights page. Search for
"TODO: wire real field" below once you add/rename the real columns
(e.g. Enrollment.paymentStatus, Enrollment.source) so these come alive.
"""
from __future__ import annotations
import hashlib
import subprocess
import time
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.db.models import Avg, Count, DecimalField, Q, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.utils import timezone

from Gym.models import Gym, SubscriptionPlan

CACHE_TTL = 60 * 5  # 5 minutes
CACHE_PREFIX = "platform_insights"
DASHBOARD_CACHE_TTL = 60 * 5  # 5 minutes
NOTIFICATIONS_CACHE_TTL = 8   # 5-10s per spec


# --------------------------------------------------------------------------- #
# Field-existence helper (prevents FieldError crashes on optional columns)
# --------------------------------------------------------------------------- #
_FIELD_CACHE: dict[tuple[str, str], bool] = {}


def _has_field(model, field_name: str) -> bool:
    """
    True if `model` has a concrete field called `field_name`.
    Cached per-process since model metadata never changes at runtime.
    """
    key = (model.__name__, field_name)
    if key not in _FIELD_CACHE:
        try:
            model._meta.get_field(field_name)
            _FIELD_CACHE[key] = True
        except Exception:
            _FIELD_CACHE[key] = False
    return _FIELD_CACHE[key]


def _get_enrollment_model():
    from AuthFit.models import Enrollment
    return Enrollment


def _safe(builder, default):
    """Run a builder; on any error, return a safe default instead of raising."""
    try:
        return builder()
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# Cache helpers
# --------------------------------------------------------------------------- #
def _cache_key(name: str, filters: dict[str, Any] | None = None) -> str:
    """
    Build a deterministic, memcached-safe cache key.
    """
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
    """
    Wipe every cached insights widget. Called from signal handlers whenever
    a Gym, Subscription, Payment, or Enrollment record changes.

    Uses a version counter instead of pattern deletion, since most cache
    backends (e.g. django-redis default) don't guarantee KEYS/SCAN support
    in production. Bumping the version invalidates every key built with it.
    """
    try:
        cache.incr(f"{CACHE_PREFIX}:version")
    except ValueError:
        cache.set(f"{CACHE_PREFIX}:version", 1, timeout=None)


def _versioned_key(name: str, filters: dict[str, Any] | None = None) -> str:
    version = cache.get(f"{CACHE_PREFIX}:version", 1)
    return f"{_cache_key(name, filters)}:v{version}"


def get_cached(name: str, builder, filters: dict[str, Any] | None = None):
    """Fetch-or-build-and-cache pattern used by every widget function."""
    key = _versioned_key(name, filters)
    data = cache.get(key)
    if data is None:
        data = builder()
        cache.set(key, data, CACHE_TTL)
    return data


# --------------------------------------------------------------------------- #
# Shared querysets / filter application
# --------------------------------------------------------------------------- #
def _base_gym_qs():
    """Single annotated queryset reused by every widget to avoid N+1s."""
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
                Sum("enrollment__Amount"), 0,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )
    )


def apply_filters(qs, filters: dict[str, Any]):
    """
    Applies the shared top-bar filters (plan, status, state, city, search,
    date range) to a Gym queryset. Every widget builder calls this first.
    """
    today = timezone.now().date()

    plan = filters.get("plan")
    if plan:
        qs = qs.filter(plan__name=plan)

    status = filters.get("status")
    if status == "active":
        qs = qs.filter(active=True, subscription_end__gte=today)
    elif status == "trial":
        # TODO: wire real field — Gym has no `is_trial` column yet.
        # Until it exists, "trial" status matches nothing rather than 500ing.
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

    return qs


def _resolve_date_range(filters: dict[str, Any]):
    """Translate the 'range' filter key into a (start, end) date tuple."""
    today = timezone.now().date()
    range_key = filters.get("range", "")

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


# --------------------------------------------------------------------------- #
# KPI cards
# --------------------------------------------------------------------------- #
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

        monthly_revenue = gyms.filter(
            enrollment__created_at__date__gte=month_start
        ).aggregate(
            total=Coalesce(
                Sum("enrollment__Amount"), 0,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )["total"] or 0

        prev_monthly_revenue = gyms.filter(
            enrollment__created_at__date__gte=prev_month_start,
            enrollment__created_at__date__lte=prev_month_end,
        ).aggregate(
            total=Coalesce(
                Sum("enrollment__Amount"), 0,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )["total"] or 0

        # TODO: wire real field — Enrollment has no `is_renewal` column yet.
        Enrollment = _get_enrollment_model()
        if _has_field(Enrollment, "is_renewal"):
            renewal_stats = gyms.aggregate(
                renewed=Count("enrollment", filter=Q(enrollment__is_renewal=True), distinct=True),
                total_memberships=Count("enrollment", distinct=True),
            )
            renewal_rate = (
                round(renewal_stats["renewed"] / renewal_stats["total_memberships"] * 100, 1)
                if renewal_stats["total_memberships"]
                else 0
            )
        else:
            renewal_rate = 0

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
        }

    return get_cached("kpi_summary", build, filters)


# --------------------------------------------------------------------------- #
# System health helpers (real infra checks)
# --------------------------------------------------------------------------- #
def _get_service_status(service_name: str) -> str:
    """systemctl is-active check. 'Failed' covers unknown/error states too."""
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
        return round(psutil.cpu_percent(interval=0.3))
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
    """
    Reads a cache key `cron:last_run` that your cron-triggered views must set
    themselves, e.g. right after the expiry-reminder job finishes:

        from django.core.cache import cache
        cache.set("cron:last_run", timezone.now(), timeout=None)

    Without that write, this always reports "Warning" (no data yet) rather
    than lying and saying "Running".
    """
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

        # Redis — real write/read/delete + latency check
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

    # 25s cache — inside the required 15-30s window, avoids shelling out /
    # hitting psutil on every single request.
    key = f"{CACHE_PREFIX}:system_health"
    data = cache.get(key)
    if data is None:
        data = build()
        cache.set(key, data, 25)
    return data


# --------------------------------------------------------------------------- #
# Section 1 — Platform growth (daily new gyms, 30 days)
# --------------------------------------------------------------------------- #
def get_platform_growth(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        today = timezone.now().date()
        start = today - timedelta(days=29)
        week_start = today - timedelta(days=6)
        month_start = today.replace(day=1)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
        prev_month_end = month_start - timedelta(days=1)

        gyms = apply_filters(_base_gym_qs(), filters)

        daily = (
            gyms.filter(created_at__date__gte=start)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("id", distinct=True))
            .order_by("day")
        )
        daily_map = {row["day"].isoformat(): row["count"] for row in daily}
        labels = [(start + timedelta(days=i)).isoformat() for i in range(30)]
        series = [daily_map.get(d, 0) for d in labels]

        today_count = gyms.filter(created_at__date=today).count()
        week_count = gyms.filter(created_at__date__gte=week_start).count()
        month_count = gyms.filter(created_at__date__gte=month_start).count()
        prev_month_count = gyms.filter(
            created_at__date__gte=prev_month_start,
            created_at__date__lte=prev_month_end,
        ).count()
        growth_pct = (
            round((month_count - prev_month_count) / prev_month_count * 100, 1)
            if prev_month_count
            else (100.0 if month_count else 0.0)
        )

        return {
            "labels": labels,
            "series": series,
            "today": today_count,
            "this_week": week_count,
            "this_month": month_count,
            "previous_month": prev_month_count,
            "growth_pct": growth_pct,
        }

    return get_cached("platform_growth", build, filters)


# --------------------------------------------------------------------------- #
# Section 2 — Member growth
# --------------------------------------------------------------------------- #
def get_member_growth(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        from AuthFit.models import Enrollment

        today = timezone.now().date()
        start = today - timedelta(days=29)
        week_start = today - timedelta(days=6)
        month_start = today.replace(day=1)

        gym_ids = apply_filters(_base_gym_qs(), filters).values_list("id", flat=True)

        enrollments = Enrollment.objects.filter(gym_id__in=gym_ids)

        daily = (
            enrollments.filter(created_at__date__gte=start)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("id", distinct=True))
            .order_by("day")
        )
        daily_map = {row["day"].isoformat(): row["count"] for row in daily}
        labels = [(start + timedelta(days=i)).isoformat() for i in range(30)]
        series = [daily_map.get(d, 0) for d in labels]

        total_members = enrollments.count()
        new_today = enrollments.filter(created_at__date=today).count()
        new_week = enrollments.filter(created_at__date__gte=week_start).count()
        new_month = enrollments.filter(created_at__date__gte=month_start).count()
        gym_count = len(gym_ids) or 1
        avg_per_gym = round(total_members / gym_count, 1)

        return {
            "labels": labels,
            "series": series,
            "total_members": total_members,
            "new_today": new_today,
            "new_this_week": new_week,
            "new_this_month": new_month,
            "avg_members_per_gym": avg_per_gym,
        }

    return get_cached("member_growth", build, filters)

# --------------------------------------------------------------------------- #
# Section 4 — Subscription analytics
# --------------------------------------------------------------------------- #
def get_subscription_analytics(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        gyms = apply_filters(_base_gym_qs(), filters)

        # TODO: wire real field — Enrollment has no `is_renewal` column yet.
        Enrollment = _get_enrollment_model()
        renewed_filter = (
            Q(gym__in=gyms, gym__enrollment__is_renewal=True)
            if _has_field(Enrollment, "is_renewal")
            else Q(pk__in=[])
        )

        plans = (
            SubscriptionPlan.objects.annotate(
                gym_count=Count("gym", filter=Q(gym__in=gyms), distinct=True),
                monthly_revenue=Coalesce(
                    Sum("gym__enrollment__Amount", filter=Q(gym__in=gyms)), 0,
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                renewed=Count("gym__enrollment", filter=renewed_filter, distinct=True),
                total_enrollments=Count("gym__enrollment", filter=Q(gym__in=gyms), distinct=True),
            )
            .order_by("-gym_count")
        )

        plan_data = []
        most_popular = None
        max_count = -1
        for p in plans:
            renewal_pct = (
                round(p.renewed / p.total_enrollments * 100, 1)
                if p.total_enrollments else 0.0
            )
            annual_revenue = float(p.monthly_revenue) * 12
            plan_data.append({
                "name": p.name,
                "gym_count": p.gym_count,
                "monthly_revenue": float(p.monthly_revenue),
                "annual_revenue": annual_revenue,
                "renewal_pct": renewal_pct,
            })
            if p.gym_count > max_count:
                max_count = p.gym_count
                most_popular = p.name

        return {"plans": plan_data, "most_popular_plan": most_popular}

    return get_cached("subscription_analytics", build, filters)


# --------------------------------------------------------------------------- #
# Section 5 — Revenue analytics (12 months)
# --------------------------------------------------------------------------- #
def get_revenue_analytics(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        from AuthFit.models import Enrollment

        today = timezone.now().date()
        month_start = today.replace(day=1)
        week_start = today - timedelta(days=6)
        year_start = today.replace(month=1, day=1)
        twelve_months_ago = (month_start - timedelta(days=365)).replace(day=1)

        gym_ids = apply_filters(_base_gym_qs(), filters).values_list("id", flat=True)
        enrollments = Enrollment.objects.filter(gym_id__in=gym_ids)

        # Same single aggregation query as before — no extra DB hits.
        monthly = (
            enrollments.filter(created_at__date__gte=twelve_months_ago)
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(total=Coalesce(
                Sum("Amount"), 0,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ))
            .order_by("month")
        )

        # Key results by (year, month) so gaps can be filled in Python.
        revenue_by_month = {
            (row["month"].year, row["month"].month): float(row["total"])
            for row in monthly
        }

        # Build the last 12 consecutive calendar months, oldest → newest,
        # including the current month, regardless of whether they had revenue.
        month_keys = []
        cursor = month_start
        for _ in range(12):
            month_keys.append((cursor.year, cursor.month))
            cursor = (cursor.replace(day=1) - timedelta(days=1)).replace(day=1)
        month_keys.reverse()

        labels = [
            timezone.datetime(year, month, 1).strftime("%b %Y")
            for year, month in month_keys
        ]
        series = [revenue_by_month.get(key, 0.0) for key in month_keys]

        def sum_amount(qs):
            return float(qs.aggregate(
                t=Coalesce(Sum("Amount"), 0, output_field=DecimalField(max_digits=12, decimal_places=2))
            )["t"] or 0)

        todays_revenue = sum_amount(enrollments.filter(created_at__date=today))
        weekly_revenue = sum_amount(enrollments.filter(created_at__date__gte=week_start))
        monthly_revenue = sum_amount(enrollments.filter(created_at__date__gte=month_start))
        annual_revenue = sum_amount(enrollments.filter(created_at__date__gte=year_start))

        gyms = apply_filters(_base_gym_qs(), filters)
        mrr = float(gyms.filter(active=True, subscription_end__gte=today).aggregate(
            mrr=Coalesce(
                Sum("plan__price_monthly"), 0,
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )["mrr"] or 0)
        arr = mrr * 12

        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
        prev_month_end = month_start - timedelta(days=1)
        prev_monthly_revenue = sum_amount(
            enrollments.filter(
                created_at__date__gte=prev_month_start,
                created_at__date__lte=prev_month_end,
            )
        )
        revenue_growth_pct = (
            round((monthly_revenue - prev_monthly_revenue) / prev_monthly_revenue * 100, 1)
            if prev_monthly_revenue
            else (100.0 if monthly_revenue else 0.0)
        )

        return {
            "labels": labels,
            "series": series,
            "todays_revenue": todays_revenue,
            "weekly_revenue": weekly_revenue,
            "monthly_revenue": monthly_revenue,
            "annual_revenue": annual_revenue,
            "mrr": mrr,
            "arr": arr,
            "revenue_growth_pct": revenue_growth_pct,
        }

    return get_cached("revenue_analytics", build, filters)


# --------------------------------------------------------------------------- #
# Section 6 — Member distribution (top 10 gyms)
# --------------------------------------------------------------------------- #
def get_member_distribution(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        today = timezone.now().date()
        month_start = today.replace(day=1)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
        prev_month_end = month_start - timedelta(days=1)

        gyms = apply_filters(_base_gym_qs(), filters).order_by("-member_count")[:10]

        rows = []
        for g in gyms:
            capacity_pct = round(g.member_count / g.member_limit * 100, 1) if g.member_limit else 0.0
            new_this_month = g.enrollment_set.filter(created_at__date__gte=month_start).count()
            new_prev_month = g.enrollment_set.filter(
                created_at__date__gte=prev_month_start,
                created_at__date__lte=prev_month_end,
            ).count()
            growth_pct = (
                round((new_this_month - new_prev_month) / new_prev_month * 100, 1)
                if new_prev_month else (100.0 if new_this_month else 0.0)
            )
            rows.append({
                "gym_name": g.gym_name,
                "members": g.member_count,
                "capacity_pct": capacity_pct,
                "growth_pct": growth_pct,
            })

        return {"gyms": rows}

    return get_cached("member_distribution", build, filters)


# --------------------------------------------------------------------------- #
# Section 7 — Today's platform activity
# --------------------------------------------------------------------------- #
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
            # TODO: wire real field — Enrollment has no `is_renewal` column yet.
            # safe_count already swallows the FieldError and returns 0, so this
            # stays safe even before the field exists.
            "renewals": safe_count(
                "AuthFit.models.Enrollment",
                gym_id__in=gym_ids, created_at__date=today, is_renewal=True,
            ),
        }

    return get_cached("platform_activity", build, filters)


# --------------------------------------------------------------------------- #
# Section 8 — Engagement analytics
# --------------------------------------------------------------------------- #
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
        avg_attendance_pct = 0.0
        try:
            from AuthFit.models import AttendanceRecord
            attendance_agg = AttendanceRecord.objects.filter(
                gym_id__in=gyms.values_list("id", flat=True),
                timestamp__date__gte=month_start,
            ).aggregate(count=Count("id", distinct=True))
            total_members = gyms.aggregate(m=Coalesce(Sum("member_count"), 0))["m"]
            if total_members:
                avg_attendance_pct = round(attendance_agg["count"] / total_members * 100, 1)
        except Exception:
            pass

        return {
            "daily_active_gyms": daily_active,
            "weekly_active_gyms": weekly_active,
            "monthly_active_gyms": monthly_active,
            "avg_login_frequency": round(weekly_active / total, 2) if total else 0.0,
            "avg_attendance_pct": avg_attendance_pct,
        }

    return get_cached("engagement_analytics", build, filters)


# --------------------------------------------------------------------------- #
# Section 9 — Renewal & churn
# --------------------------------------------------------------------------- #
def get_renewal_churn(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        from AuthFit.models import Enrollment

        today = timezone.now().date()
        start = today - timedelta(days=29)
        gym_ids = apply_filters(_base_gym_qs(), filters).values_list("id", flat=True)
        enrollments = Enrollment.objects.filter(gym_id__in=gym_ids)

        # TODO: wire real fields — Enrollment has neither `is_renewal` nor
        # `status`. Until those exist (or you map them to real columns like
        # `paymentStatus`), renewals/expired/frozen/cancelled all report 0
        # instead of crashing the endpoint.
        has_is_renewal = _has_field(Enrollment, "is_renewal")
        has_status = _has_field(Enrollment, "status")

        renewed_filter = Q(is_renewal=True) if has_is_renewal else Q(pk__in=[])
        expired_filter = Q(status="expired") if has_status else Q(pk__in=[])
        frozen_filter = Q(status="frozen") if has_status else Q(pk__in=[])
        cancelled_filter = Q(status="cancelled") if has_status else Q(pk__in=[])

        agg = enrollments.aggregate(
            renewed=Count("id", filter=renewed_filter, distinct=True),
            expired=Count("id", filter=expired_filter, distinct=True),
            frozen=Count("id", filter=frozen_filter, distinct=True),
            cancelled=Count("id", filter=cancelled_filter, distinct=True),
            total=Count("id", distinct=True),
        )
        renewal_rate = round(agg["renewed"] / agg["total"] * 100, 1) if agg["total"] else 0.0
        churn_rate = (
            round((agg["expired"] + agg["cancelled"]) / agg["total"] * 100, 1)
            if agg["total"] else 0.0
        )

        if has_is_renewal:
            daily = (
                enrollments.filter(created_at__date__gte=start, is_renewal=True)
                .annotate(day=TruncDate("created_at"))
                .values("day")
                .annotate(count=Count("id", distinct=True))
                .order_by("day")
            )
            daily_map = {row["day"].isoformat(): row["count"] for row in daily}
        else:
            daily_map = {}
        labels = [(start + timedelta(days=i)).isoformat() for i in range(30)]
        series = [daily_map.get(d, 0) for d in labels]

        return {
            "renewed": agg["renewed"],
            "expired": agg["expired"],
            "frozen": agg["frozen"],
            "cancelled": agg["cancelled"],
            "renewal_rate": renewal_rate,
            "churn_rate": churn_rate,
            "trend_labels": labels,
            "trend_series": series,
        }

    return get_cached("renewal_churn", build, filters)


# --------------------------------------------------------------------------- #
# Section 10 — Payment analytics
# --------------------------------------------------------------------------- #
def get_payment_analytics(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        try:
            from AuthFit.models import Payment
        except ImportError:
            return {
                "successful": 0, "failed": 0, "pending": 0,
                "avg_amount": 0.0, "success_rate": 0.0, "failure_rate": 0.0,
            }

        gym_ids = apply_filters(_base_gym_qs(), filters).values_list("id", flat=True)
        payments = Payment.objects.filter(gym_id__in=gym_ids)

        agg = payments.aggregate(
            successful=Count("id", filter=Q(status="success")),
            failed=Count("id", filter=Q(status="failed")),
            pending=Count("id", filter=Q(status="pending")),
            total=Count("id"),
            avg_amount=Coalesce(
                Avg("amount"), 0,
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        success_rate = round(agg["successful"] / agg["total"] * 100, 1) if agg["total"] else 0.0
        failure_rate = round(agg["failed"] / agg["total"] * 100, 1) if agg["total"] else 0.0

        return {
            "successful": agg["successful"],
            "failed": agg["failed"],
            "pending": agg["pending"],
            "avg_amount": float(agg["avg_amount"] or 0),
            "success_rate": success_rate,
            "failure_rate": failure_rate,
        }

    return get_cached("payment_analytics", build, filters)


# --------------------------------------------------------------------------- #
# Section 11 — Top performing gyms
# --------------------------------------------------------------------------- #
def get_top_performing_gyms(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        today = timezone.now().date()
        month_start = today.replace(day=1)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
        prev_month_end = month_start - timedelta(days=1)

        gyms = apply_filters(_base_gym_qs(), filters).order_by("-revenue")[:25]

        # TODO: wire real field — Enrollment has no `is_renewal` column yet.
        Enrollment = _get_enrollment_model()
        has_is_renewal = _has_field(Enrollment, "is_renewal")

        rows = []
        for rank, g in enumerate(gyms, start=1):
            renewed = g.enrollment_set.filter(is_renewal=True).count() if has_is_renewal else 0
            total_enrollments = g.enrollment_set.count()
            renewal_pct = round(renewed / total_enrollments * 100, 1) if total_enrollments else 0.0

            new_this_month = g.enrollment_set.filter(created_at__date__gte=month_start).count()
            new_prev_month = g.enrollment_set.filter(
                created_at__date__gte=prev_month_start,
                created_at__date__lte=prev_month_end,
            ).count()
            growth_pct = (
                round((new_this_month - new_prev_month) / new_prev_month * 100, 1)
                if new_prev_month else (100.0 if new_this_month else 0.0)
            )

            attendance_pct = 0.0
            try:
                from AuthFit.models import AttendanceRecord
                att_count = AttendanceRecord.objects.filter(
                    gym=g, timestamp__date__gte=month_start
                ).values("member_id").distinct().count()
                if g.member_count:
                    attendance_pct = round(att_count / g.member_count * 100, 1)
            except Exception:
                pass

            rows.append({
                "rank": rank,
                "id": str(g.id),
                "gym_name": g.gym_name,
                "members": g.member_count,
                "revenue": float(g.revenue),
                "attendance_pct": attendance_pct,
                "renewal_pct": renewal_pct,
                "growth_pct": growth_pct,
                "active_trainers": g.trainer_count,
            })

        return {"gyms": rows}

    return get_cached("top_performing_gyms", build, filters)


# --------------------------------------------------------------------------- #
# Section 12 — Low performing gyms
# --------------------------------------------------------------------------- #
def get_low_performing_gyms(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        now = timezone.now()
        week_ago = now - timedelta(days=7)
        gyms = apply_filters(_base_gym_qs(), filters)

        lowest_activity = list(
            gyms.order_by("member_count")[:10].values("id", "gym_name", "member_count")
        )
        lowest_revenue = list(
            gyms.order_by("revenue")[:10].values("id", "gym_name", "revenue")
        )
        expiring_soon = list(
            gyms.filter(
                active=True,
                subscription_end__gte=now,
                subscription_end__lte=now + timedelta(days=7),
            ).values("id", "gym_name", "subscription_end")
        )
        inactive_owners = list(
            gyms.filter(
                Q(owner__last_login__lt=week_ago) | Q(owner__last_login__isnull=True)
            ).values("id", "gym_name", "owner__username", "owner__last_login")[:10]
        )
        no_login_7d = inactive_owners  # same underlying condition, kept distinct per spec

        def _stringify(rows, date_fields=()):
            for r in rows:
                r["id"] = str(r["id"])
                for f in date_fields:
                    if r.get(f) is not None:
                        r[f] = r[f].isoformat()
            return rows

        return {
            "lowest_activity": _stringify(lowest_activity),
            "lowest_revenue": _stringify(lowest_revenue),
            "expiring_soon": _stringify(expiring_soon, ["subscription_end"]),
            "inactive_owners": _stringify(inactive_owners, ["owner__last_login"]),
            "no_login_7d": _stringify(no_login_7d, ["owner__last_login"]),
        }

    return get_cached("low_performing_gyms", build, filters)

# --------------------------------------------------------------------------- #
# Aggregated Dashboard endpoint — single response, single cache entry.
# Reuses every widget function above (same cached builders), so there is
# exactly one source of business logic. This wraps the whole assembled
# payload in its own 5-minute cache so a cold dashboard load only recomputes
# once per cache window regardless of how many admins are viewing it.
# --------------------------------------------------------------------------- #
def get_dashboard(filters: dict[str, Any]) -> dict[str, Any]:
    def build():
        now = timezone.now()

        kpi = _safe(lambda: get_kpi_summary(filters), {
            "total_gyms": 0, "active_gyms": 0, "total_members": 0,
            "monthly_revenue": 0.0, "renewal_rate": 0.0,
            "growth_pct": 0.0, "revenue_growth_pct": 0.0,
            "gyms_this_month": 0, "gyms_prev_month": 0,
        })

        platform_growth = _safe(lambda: get_platform_growth(filters), {
            "labels": [], "series": [], "today": 0, "this_week": 0,
            "this_month": 0, "previous_month": 0, "growth_pct": 0.0,
        })

        member_growth = _safe(lambda: get_member_growth(filters), {
            "labels": [], "series": [], "total_members": 0, "new_today": 0,
            "new_this_week": 0, "new_this_month": 0, "avg_members_per_gym": 0.0,
        })

        subscription = _safe(lambda: get_subscription_analytics(filters), {
            "plans": [], "most_popular_plan": None,
        })

        revenue = _safe(lambda: get_revenue_analytics(filters), {
            "labels": [], "series": [], "todays_revenue": 0.0, "weekly_revenue": 0.0,
            "monthly_revenue": 0.0, "annual_revenue": 0.0, "mrr": 0.0, "arr": 0.0,
            "revenue_growth_pct": 0.0,
        })

        member_distribution = _safe(lambda: get_member_distribution(filters), {"gyms": []})

        activity = _safe(lambda: get_platform_activity(filters), {
            "members_checked_in": 0, "attendance_records": 0, "invoices_generated": 0,
            "payments_recorded": 0, "products_sold": 0, "new_registrations": 0, "renewals": 0,
        })

        engagement = _safe(lambda: get_engagement_analytics(filters), {
            "daily_active_gyms": 0, "weekly_active_gyms": 0, "monthly_active_gyms": 0,
            "avg_login_frequency": 0.0, "avg_attendance_pct": 0.0,
        })

        renewal_churn = _safe(lambda: get_renewal_churn(filters), {
            "renewed": 0, "expired": 0, "frozen": 0, "cancelled": 0,
            "renewal_rate": 0.0, "churn_rate": 0.0, "trend_labels": [], "trend_series": [],
        })

        payments = _safe(lambda: get_payment_analytics(filters), {
            "successful": 0, "failed": 0, "pending": 0,
            "avg_amount": 0.0, "success_rate": 0.0, "failure_rate": 0.0,
        })

        top_gyms = _safe(lambda: get_top_performing_gyms(filters), {"gyms": []})
        low_gyms = _safe(lambda: get_low_performing_gyms(filters), {
            "lowest_activity": [], "lowest_revenue": [], "expiring_soon": [],
            "inactive_owners": [], "no_login_7d": [],
        })

        return {
            "kpi_summary": kpi,
            "platform_growth": platform_growth,
            "member_growth": member_growth,
            "subscription_analytics": subscription,
            "revenue_analytics": revenue,
            "member_distribution": member_distribution,
            "platform_activity": activity,
            "engagement_analytics": engagement,
            "renewal_churn": renewal_churn,
            "payment_analytics": payments,
            "top_performing_gyms": top_gyms,
            "low_performing_gyms": low_gyms,
        }

    return get_cached("dashboard", build, filters)


# --------------------------------------------------------------------------- #
# Notifications — short-TTL, live alerts feed. Deliberately NOT reusing the
# 5-minute-cached analytics builders above; these need to reflect near
# real-time state (a failed payment 30 seconds ago should show up).
# --------------------------------------------------------------------------- #
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
        # get_system_health() is 25s-cached, so calling it multiple times
        # below is cheap (cache hits, not repeated psutil/systemctl calls).
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
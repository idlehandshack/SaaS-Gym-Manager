from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.db.models import Sum, Count, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone

from AuthFit.models import Gym, Enrollment, Attendence

CACHE_KEY = "public_live_stats_v3"  # bumped — old cached entries used paidAmount
CACHE_TTL_SECONDS = 120


def _safe_float(value) -> float:
    """Guards against Decimal('NaN') / Decimal('Infinity') leaking into JSON."""
    try:
        f = float(value)
    except (TypeError, ValueError, InvalidOperation):
        return 0.0
    if math.isnan(f) or math.isinf(f):
        return 0.0
    return f


def _compute_live_stats() -> dict:
    today = timezone.now().date()

    members = Enrollment.objects.filter(is_deleted=False).aggregate(
        total=Coalesce(Count("id"), 0)
    )["total"]
    total_revenue = Enrollment.objects.filter(
        is_deleted=False,
    ).aggregate(
        total=Coalesce(
            Sum("Amount"), 0,
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )["total"] or 0

    gyms_live = Gym.objects.filter(
        active=True,
        subscription_end__gte=today,
    ).count()

    data = {
        "members": members,
        "gyms": gyms_live,
        "revenue": _safe_float(total_revenue),
        "uptime": 99.98,
        "today_checkins": Attendence.objects.filter(date=today).count(),
    }

    return data


def get_live_stats() -> dict:
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    data = _compute_live_stats()
    cache.set(CACHE_KEY, data, CACHE_TTL_SECONDS)
    return data
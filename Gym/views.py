from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum, Q, DecimalField
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone
from datetime import timedelta

from Gym.models import Gym, SubscriptionPlan


def superuser_required(view_func):
    """Only Django superusers can pass. Everyone else gets 403."""
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper


@superuser_required
def saas_dashboard(request):
    today = timezone.now().date()
    month_start = today.replace(day=1)

    gyms = (
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
                output_field=DecimalField(max_digits=12, decimal_places=2)
            ),
        )
        .order_by("-created_at")
    )

    total_gyms     = gyms.count()
    active_gyms    = gyms.filter(active=True, subscription_end__gte=today).count()
    inactive_gyms  = total_gyms - active_gyms
    active_pct     = round(active_gyms / total_gyms * 100) if total_gyms else 0
    inactive_pct   = 100 - active_pct
    new_this_month = gyms.filter(created_at__date__gte=month_start).count()
    total_owners   = gyms.values("owner").distinct().count()

    expiring_7    = gyms.filter(active=True, subscription_end__gte=today, subscription_end__lte=today + timedelta(days=7)).count()
    expiring_15   = gyms.filter(active=True, subscription_end__gt=today + timedelta(days=7),  subscription_end__lte=today + timedelta(days=15)).count()
    expiring_30   = gyms.filter(active=True, subscription_end__gt=today + timedelta(days=15), subscription_end__lte=today + timedelta(days=30)).count()
    expired_count = gyms.filter(Q(active=False) | Q(subscription_end__lt=today)).count()

    capacity           = gyms.aggregate(
        total_members=Coalesce(Sum("member_count"), 0),
        total_member_limit=Coalesce(Sum("member_limit"), 0),
    )
    total_members      = capacity["total_members"]
    total_member_limit = capacity["total_member_limit"]
    near_member_limit  = sum(
        1 for g in gyms
        if g.member_limit and g.member_count / g.member_limit >= 0.85
    )

    rev           = gyms.aggregate(
        total=Coalesce(
            Sum("revenue"), 0,
            output_field=DecimalField(max_digits=14, decimal_places=2)
        )
    )
    total_revenue = rev["total"] or 0
    avg_revenue   = round(total_revenue / active_gyms) if active_gyms else 0
    estimated_mrr = gyms.filter(active=True, subscription_end__gte=today).aggregate(
        mrr=Coalesce(
            Sum("plan__price_monthly"), 0,
            output_field=DecimalField(max_digits=10, decimal_places=2)
        )
    )["mrr"] or 0

    try:
        from AuthFit.models import Enrollment
        monthly_revenue = Enrollment.objects.filter(
            gym__isnull=False, created_at__date__gte=month_start
        ).aggregate(
            total=Coalesce(
                Sum("Amount"), 0,
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )
        )["total"] or 0
    except Exception:
        monthly_revenue = 0

    plan_stats = [
        {"name": p.name, "count": gyms.filter(plan=p).count(), "monthly": p.price_monthly}
        for p in SubscriptionPlan.objects.all()
    ]

    return render(request, "saas_dashboard.html", {
        "gyms":               gyms,
        "total_gyms":         total_gyms,
        "active_gyms":        active_gyms,
        "inactive_gyms":      inactive_gyms,
        "expiring_7":         expiring_7,
        "expiring_15":        expiring_15,
        "expiring_30":        expiring_30,
        "expired_count":      expired_count,
        "new_this_month":     new_this_month,
        "total_owners":       total_owners,
        "active_pct":         active_pct,
        "inactive_pct":       inactive_pct,
        "near_member_limit":  near_member_limit,
        "total_members":      total_members,
        "total_member_limit": total_member_limit,
        "total_revenue":      total_revenue,
        "monthly_revenue":    monthly_revenue,
        "avg_revenue":        avg_revenue,
        "estimated_mrr":      estimated_mrr,
        "plan_stats":         plan_stats,
        "top_gyms":           gyms.order_by("-revenue")[:5],
        "top_growing":        gyms.filter(active=True).order_by("-member_count")[:6],
        "BASE_DOMAIN":        "entergym.in",
    })
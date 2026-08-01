# Gym/dashboard_views.py
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncDay, TruncMonth
from django.urls import reverse
from urllib.parse import urlencode
import functools
from AuthFit.views import _gym_staff_required  
from AuthFit.models import Enrollment, Attendence as Attendence_model, Trainer
from Gym.models import StaffProfile
from billing.services import revenue_service
from Gym.ai_credit_service import get_or_create_wallet
from django.db.models.functions import TruncDate
from AuthFit.models import RegisterScanImport
from Gym.models import AICreditTransaction
from django.http import JsonResponse

def _member_list_url(filter_key, sort_key):
    base = reverse('member_list')
    return f"{base}?{urlencode({'filter': filter_key, 'sort': sort_key})}"


def _staff_dashboard_required(view_fn):
    @_gym_staff_required
    @functools.wraps(view_fn)
    def wrapped(request, *args, **kwargs):
        if request.is_super_admin:
            return view_fn(request, *args, **kwargs)
        if request.staff_role not in ('gym_owner', 'receptionist'):
            raise PermissionDenied("This portal is for gym owners and receptionists only.")
        return view_fn(request, *args, **kwargs)
    return wrapped


def _month_labels_and_key(n_months, today):
    months = []
    y, m = today.year, today.month
    for _ in range(n_months):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()
    start = months[0]
    start_date = today.replace(year=start[0], month=start[1], day=1)
    return start_date, months


def _build_dashboard_context(request):
    gym = getattr(request, 'gym', None)
    ai_wallet = get_or_create_wallet(gym) if gym else None
    today = timezone.localdate()
    month_start = today.replace(day=1)
    yesterday = today - timedelta(days=1)
    gst_enabled = revenue_service.is_gst_enabled(gym)
    enroll_qs = Enrollment.objects.filter(gym=gym, is_deleted=False) if gym else Enrollment.objects.none()
    attend_qs = Attendence_model.objects.filter(gym=gym) if gym else Attendence_model.objects.none()

    active_members = enroll_qs.filter(is_deleted=False, DueDate__gte=today).count()
    unregistered_members = enroll_qs.filter(is_deleted=False, user__isnull=True).count()
    today_attendance = attend_qs.filter(date=today).count()
    today_figures = revenue_service.get_today_figures(gym)
    yesterday_revenue = revenue_service.get_revenue_for_range(gym, yesterday, yesterday)
    month_figures = revenue_service.get_month_figures(gym, today)

    pending_payments = enroll_qs.filter(paymentStatus='Pending').count()
    pending_amount = (
        enroll_qs.filter(paymentStatus='Pending')
        .aggregate(total=Sum('pendingAmount'))['total'] or 0
    )
    expiring_today_qs = enroll_qs.filter(DueDate=today)
    expiring_today = expiring_today_qs.count()

    expired_members = enroll_qs.filter(is_deleted=False, DueDate__lt=today).count()
    notifications = _build_notifications(request, gym)
    
    new_members_month = enroll_qs.filter(doj__gte=month_start, doj__lte=today).count()
    renewals_today = enroll_qs.filter(paymentDate=today, paymentStatus='Done').count()
    hidden_cards = set(gym.hidden_stat_cards or []) if gym else set()
    today_rev_f = float(today_figures['revenue'])
    yesterday_rev_f = float(yesterday_revenue)
    if yesterday_rev_f:
        revenue_change_pct = round(((today_rev_f - yesterday_rev_f) / yesterday_rev_f) * 100, 1)
    else:
        revenue_change_pct = 100.0 if today_rev_f else 0.0
    revenue_trend_up = today_rev_f >= yesterday_rev_f

    stats = {
        "active_members": active_members,
        "unregistered_members": unregistered_members,
        "today_attendance": today_attendance,
        "today_revenue_display": f"₹{today_figures['revenue']:,.0f}",
        "today_collection_display": f"₹{today_figures['collection']:,.0f}",
        "month_revenue_display": f"₹{month_figures['revenue']:,.0f}",
        "month_collection_display": f"₹{month_figures['collection']:,.0f}",
        "pending_payments": pending_payments,
        "pending_amount_display": f"₹{pending_amount:,.0f}",
        "expiring_today": expiring_today,
        "expired_members": expired_members,
        "new_members_month": new_members_month,
        "renewals_today": renewals_today,
        "revenue_change_pct": revenue_change_pct,
        "revenue_trend_up": revenue_trend_up,
        "ai_credits_balance": ai_wallet.balance if ai_wallet else 0,
        "ai_credits_used": ai_wallet.total_used if ai_wallet else 0,
        "ai_credits_low": bool(ai_wallet and 0 < ai_wallet.balance <= 3),
        "ai_credits_zero": bool(ai_wallet and ai_wallet.balance == 0),
    }
    member_list_urls = {
        "active":           _member_list_url("active", "expiry_date"),
        "unregistered":     _member_list_url("unregistered", "newest"), 
        "pending":          _member_list_url("pending", "expiry_date"),
        "attendance_today": _member_list_url("attendance_today", "name"),
        "new_month":        _member_list_url("new_month", "newest"),
        "renewals_today":   _member_list_url("renewals_today", "newest"),
        "expiring_today":   _member_list_url("expiring_today", "name"),
        "all":              _member_list_url("all", "name"),
    }
    expiry_today = list(expiring_today_qs.order_by('fullname')[:8])
    expiry_tomorrow = list(enroll_qs.filter(DueDate=today + timedelta(days=1)).order_by('fullname')[:8])
    expiry_week = list(
        enroll_qs.filter(DueDate__gt=today, DueDate__lte=today + timedelta(days=7))
        .order_by('DueDate')[:8]
    )
    recent_activity = []
    for e in enroll_qs.order_by('-created_at')[:5]:
        recent_activity.append({
            "icon": "bi-person-plus-fill",
            "text": f"{e.fullname} joined ({e.selectPlan.plan if e.selectPlan else '—'})",
            "when": timezone.localtime(e.created_at).strftime("%d %b, %I:%M %p") if e.created_at else "",
        })
    recent_activity.sort(key=lambda x: x["when"], reverse=True)
    recent_activity = recent_activity[:6]
    daily_series = revenue_service.get_daily_series(gym, days=14, metric='revenue')
    revenue_labels = [r['date'].strftime('%d %b') for r in daily_series]
    revenue_data = [float(r['value']) for r in daily_series]
    membership_status_labels = ['Active', 'Expired', 'Pending Payment']
    membership_status_data = [
        enroll_qs.filter(DueDate__gte=today).count(),
        enroll_qs.filter(DueDate__lt=today).count(),
        pending_payments,
    ]
    monthly_series = revenue_service.get_monthly_series(gym, months=6, metric='collection')
    collection_labels = [
        f"{today.replace(year=r['year'], month=r['month'], day=1):%b %Y}" for r in monthly_series
    ]
    collection_data = [float(r['value']) for r in monthly_series]
    mem_start, mem_months = _month_labels_and_key(6, today)
    mem_rows = (
        enroll_qs.filter(doj__gte=mem_start, doj__lte=today)
        .annotate(month=TruncMonth('doj'))
        .values('month').annotate(total=Count('id'))
    )
    mem_map = {(r['month'].year, r['month'].month): r['total'] for r in mem_rows if r['month']}
    members_monthly_labels = [f"{today.replace(year=y, month=m, day=1):%b %Y}" for (y, m) in mem_months]
    members_monthly_data = [mem_map.get((y, m), 0) for (y, m) in mem_months]
    
    return {
        "gym": gym,
        "hidden_cards": hidden_cards,
        "active": "dashboard",
        "gst_enabled": gst_enabled, 
        "stats": stats,
        "member_list_urls": member_list_urls,
        "expiry_today": expiry_today,
        "expiry_tomorrow": expiry_tomorrow,
        "expiry_week": expiry_week,
        "recent_activity": recent_activity,
        "expiring_today_count": expiring_today,
        "revenue_labels": revenue_labels,
        "revenue_data": revenue_data,
        "membership_status_labels": membership_status_labels,
        "membership_status_data": membership_status_data,
        "collection_labels": collection_labels,
        "collection_data": collection_data,
        "members_monthly_labels": members_monthly_labels,
        "members_monthly_data": members_monthly_data,
        "notifications": notifications,
        "notification_count": len(notifications),
    }
_CHART_JSON_KEYS = (
    "revenue_labels", "revenue_data",
    "membership_status_labels", "membership_status_data",
    "collection_labels", "collection_data",
    "members_monthly_labels", "members_monthly_data",
)


@_staff_dashboard_required
def dashboard_home(request):
    import json
    context = _build_dashboard_context(request)
    context["today"] = timezone.localdate()
    for key in _CHART_JSON_KEYS:
        context[key] = json.dumps(context[key])
    return render(request, "dashboard/dashboard.html", context)


def _build_notifications(request, gym):
    notifications = []

    if gym is None:
        return notifications

    role = getattr(request, 'staff_role', None)
    if role not in ('gym_owner', 'receptionist'):
        return notifications
    member_limit = gym.member_limit or 0
    current_members = Enrollment.objects.filter(gym=gym, is_deleted=False).count()
    remaining_slots = max(0, member_limit - current_members)
    usage_percent = round((current_members / member_limit) * 100) if member_limit else 0

    if current_members > member_limit:
        exceeded_count = current_members - member_limit
        severity = 'danger'
        title = "Member Limit Exceeded"
        message = f"{exceeded_count} member{'s' if exceeded_count != 1 else ''} exceed your subscription limit."
    elif remaining_slots == 0:
        severity = 'danger'
        title = "Member Limit Reached"
        message = "Member limit reached. No additional members can be created."
    elif remaining_slots <= 10:
        severity = 'warning'
        title = "Member Limit"
        message = f"Only {remaining_slots} member slot{'s' if remaining_slots != 1 else ''} remaining."
    else:
        severity = 'info'
        title = "Member Limit"
        message = f"You have {remaining_slots} member slots remaining."

    notifications.append({
        "id": "member_limit",
        "type": "member_limit",
        "severity": severity,
        "icon": "bi-people-fill",
        "title": title,
        "current_members": current_members,
        "member_limit": member_limit,
        "remaining_slots": remaining_slots,
        "usage_percent": usage_percent,   # ← added, was missing
        "message": message,
        "action_url": _member_list_url("all", "name"),
        "action_label": "View Members",
    })
    if getattr(gym, 'show_subscription_payment', False):
        notifications.append({
            "id": "subscription_payment",
            "type": "subscription_payment",
            "severity": "danger",
            "icon": "bi-credit-card-fill",
            "title": "Subscription Payment Due",
            "message": "Your subscription payment is pending. Please pay to continue uninterrupted service.",
            "action_url": reverse('gym_payment_page'),
            "action_label": "Pay Subscription",
        })
    return notifications

@_staff_dashboard_required
def ai_credit_analysis(request):
    gym = getattr(request, 'gym', None)
    if gym is None:
        return JsonResponse({"ok": False, "error": "No gym context available."}, status=403)
    transactions = AICreditTransaction.objects.filter(gym=gym).order_by('-created_at')[:10]
    tx_data = [{
        "credits": t.credits,
        "balance_after": t.balance_after,
        "reason": t.reason,
        "created_by": (t.created_by.get_full_name() or t.created_by.username) if t.created_by else "System",
        "created_at": timezone.localtime(t.created_at).strftime("%d %b %Y %I:%M %p"),
    } for t in transactions]
    today = timezone.localdate()
    start = today - timedelta(days=6)

    rows = (
        RegisterScanImport.objects
        .filter(gym=gym, created_at__date__gte=start, created_at__date__lte=today)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(
            credits_used=Count('id', filter=Q(credit_consumed=True)),
            attendance_saved=Sum('saved_count'),
        )
    )
    by_day = {r['day']: r for r in rows}

    daily = []
    for i in range(7):
        d = start + timedelta(days=i)
        r = by_day.get(d)
        daily.append({
            "date": d.strftime("%d %b"),
            "credits_used": r['credits_used'] if r else 0,
            "attendance_saved": (r['attendance_saved'] or 0) if r else 0,
        })

    total_credits_used = sum(d['credits_used'] for d in daily)
    total_attendance = sum(d['attendance_saved'] for d in daily)
    avg_attendance_per_credit = (
        round(total_attendance / total_credits_used, 1) if total_credits_used else 0
    )

    return JsonResponse({
        "ok": True,
        "transactions": tx_data,
        "daily": daily,
        "insight": {
            "total_credits_used_7d": total_credits_used,
            "total_attendance_7d": total_attendance,
            "avg_attendance_per_credit": avg_attendance_per_credit,
        },
    })
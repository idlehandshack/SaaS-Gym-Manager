# Gym/context_processors.py
"""
Context processors available to every dashboard template (registered in
settings.TEMPLATES). Kept separate from dashboard_views.py because context
processors run on EVERY request across the whole site, not just the
dashboard-home view — so this must stay cheap and self-contained.
"""

from AuthFit.models import Enrollment
from django.urls import reverse

def notification_bell(request):
    """
    Powers the header notification bell dropdown on every dashboard page.

    Visible only to Gym Owners and Receptionists. Deliberately mirrors the
    notification-building logic that used to live in dashboard_views.py's
    _build_notifications() — moved here so it runs site-wide instead of
    only on /dashboard/. If dashboard_views still has its own copy, remove
    it and have _build_dashboard_context() reuse these same keys instead
    of recomputing them, to avoid the two drifting apart.

    Returns {} (no keys) for anonymous/non-staff/non-gym requests so
    templates can safely do `{% if notification_count %}` without extra
    guards.
    """
    gym = getattr(request, 'gym', None)
    role = getattr(request, 'staff_role', None)

    if gym is None or role not in ('gym_owner', 'receptionist'):
        return {"notifications": [], "notification_count": 0}

    notifications = []

    # ── Member Limit notification ───────────────────────────────────
    member_limit = gym.member_limit or 0
    current_members = Enrollment.objects.filter(gym=gym, is_deleted=False).count()
    remaining_slots = max(0, member_limit - current_members)
    usage_percent = min(100, round((current_members / member_limit) * 100)) if member_limit else 100
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
        "usage_percent": usage_percent,
        "message": message,
        "action_url": reverse('member_list'),
        "action_label": "View Members",
    })

    # Future notification types (Trainer Limit, Subscription Expiry,
    # WhatsApp Disconnected, Payment Reminder, Low Storage, Backup Failed,
    # Face Recognition Disabled, Attendance Device Offline) each get
    # appended here as their own block — frontend needs no changes.

    return {
        "notifications": notifications,
        "notification_count": len(notifications),
    }
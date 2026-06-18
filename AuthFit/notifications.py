# AuthFit/notifications.py
"""
Push-notification helpers for plan-expiry reminders.

Usage (called by cron via /internal/run-expiry-check/):
    from AuthFit.notifications import send_expiry_reminders
    send_expiry_reminders()
"""

import logging
from datetime import timedelta
from django.utils import timezone

from Shop.notifications import send_push_to_tokens
# NOTE: notify_staff_new_enrollment is NOT used here — it lives in views.py
# and is called via transaction.on_commit() after enrollment save.

from .models import Enrollment, UserDevice

logger = logging.getLogger(__name__)

REMINDER_WINDOW_DAYS = 3   # start notifying 3 days before expiry
OVERDUE_CUTOFF_DAYS  = 2   # stop notifying 2 days after expiry


def send_expiry_reminders() -> int:
    """
    Sends FCM expiry reminders to all members across ALL gyms whose
    membership is expiring soon or just expired.

    Called by the cron endpoint — no request context, no gym on the thread.
    Scopes UserDevice lookups by gym to avoid cross-gym token leakage.

    Returns the number of enrollments that received a push notification.
    """
    today = timezone.localdate()

    enrollments = (
        Enrollment.objects
        .filter(
            DueDate__isnull=False,
            DueDate__lte=today + timedelta(days=REMINDER_WINDOW_DAYS),
            DueDate__gte=today - timedelta(days=OVERDUE_CUTOFF_DAYS),
        )
        .exclude(last_expiry_notif_sent=today)
        .select_related('user', 'selectPlan', 'gym')
    )

    sent_count  = 0
    total_count = 0   # FIX: track count in loop — avoids second DB query

    for enr in enrollments:
        total_count += 1

        # ── Member's FCM tokens — scoped to their gym ────────────────────
        tokens = list(
            UserDevice.objects
            .filter(
                user=enr.user,
                gym=enr.gym,
                active=True,
            )
            .values_list('fcm_token', flat=True)
        )

        title, body = _build_message(enr, today)

        if tokens:
            # ── Push to member's device ───────────────────────────────────
            try:
                send_push_to_tokens(
                    tokens=tokens,
                    title=title,
                    body=body,
                    data={
                        "enrollment_id": str(enr.id),
                        "gym_id":        str(enr.gym_id),
                        "screen":        "Profile",
                        "type":          "plan_expiry",
                    },
                    channel_id='entergym_expiry',
                )
                sent_count += 1
            except Exception:
                logger.exception(
                    "Failed to send expiry push for enrollment_id=%s gym_id=%s",
                    enr.id, enr.gym_id,
                )

            # ── Notify this gym's staff via web push ──────────────────────
            try:
                _notify_gym_staff(enr, title, body)
            except Exception:
                logger.exception(
                    "Failed to send staff web push for gym_id=%s", enr.gym_id
                )

        else:
            logger.debug(
                "send_expiry_reminders: no active devices for "
                "user_id=%s gym_id=%s — skipping push",
                enr.user_id, enr.gym_id,
            )

        # ── Mark as notified today regardless of push success ────────────
        # Prevents re-notifying on next cron run even if push failed
        enr.last_expiry_notif_sent = today
        enr.save(update_fields=['last_expiry_notif_sent'])

    # FIX: was enrollments.count() — fires a second SELECT after the loop
    # already consumed the queryset. Use total_count tracked in the loop.
    logger.info(
        "Expiry reminders: processed=%d pushed=%d skipped=%d",
        total_count, sent_count, total_count - sent_count,
    )
    return sent_count


def _notify_gym_staff(enr, title, body):
    """
    Send an FCM push to all active staff devices registered at enr.gym.
    Scoped to the gym — fitzone staff never receive ironhouse alerts.
    """
    from Shop.models import StaffDevice   # avoid circular import at module level

    staff_tokens = list(
        StaffDevice.objects
        .filter(gym=enr.gym, active=True)
        .values_list('fcm_token', flat=True)
    )

    if not staff_tokens:
        logger.debug(
            "_notify_gym_staff: no staff devices for gym_id=%s", enr.gym_id
        )
        return

    send_push_to_tokens(
        tokens=staff_tokens,
        title=title,
        body=body,
        data={
            "url":    "/admin-tools/payments/",
            "type":   "plan_expiry_staff_alert",
            "gym_id": str(enr.gym_id),
        },
        channel_id='entergym_expiry',
    )


def _build_message(enr, today):
    """Build notification title + body based on days remaining."""
    days_left = (enr.DueDate - today).days
    plan_name = enr.selectPlan.plan if enr.selectPlan else "your plan"
    gym_name  = enr.gym.gym_name if enr.gym else "the gym"

    if days_left < 0:
        title = f"Membership Expired — {gym_name}"
        body  = (
            f"Your {plan_name} expired {abs(days_left)} day(s) ago. "
            f"Renew now to continue your access."
        )
    elif days_left == 0:
        title = f"Membership Expires Today — {gym_name}"
        body  = (
            f"Your {plan_name} expires today. "
            f"Renew now to avoid interruption."
        )
    elif days_left == 1:
        title = f"Membership Expires Tomorrow — {gym_name}"
        body  = (
            f"Your {plan_name} expires tomorrow. "
            f"Renew now to keep your access uninterrupted."
        )
    else:
        title = f"Membership Expiring Soon — {gym_name}"
        body  = (
            f"Your {plan_name} expires in {days_left} days. "
            f"Renew now to avoid losing access."
        )

    return title, body
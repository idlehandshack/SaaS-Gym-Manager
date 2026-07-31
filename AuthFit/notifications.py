# AuthFit/notifications.py

import logging
from collections import defaultdict
from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone

from Shop.notifications import send_push_to_tokens
from notifications.utils import send_web_push
from Gym.services import whatsapp_service
from Gym.services.whatsapp_templates import (
    TEMPLATE_MEMBERSHIP_EXPIRY_BEFORE, TEMPLATE_MEMBERSHIP_EXPIRY_AFTER,
    build_expiry_before_components, build_expiry_after_components,
)
from .models import Enrollment, UserDevice
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

REMINDER_WINDOW_DAYS = 3  # start notifying 3 days before expiry
OVERDUE_CUTOFF_DAYS  = 2  # stop notifying 2 days after expiry
WHATSAPP_MAX_REMINDER_DAYS_BEFORE = 7  
_REMINDER_TIME_TOLERANCE_MINUTES = 10

# ── Public entry point ────────────────────────────────────────────────────────

def send_expiry_reminders(gym=None) -> int:
    today = timezone.localdate()
 
    max_lookahead_days = max(REMINDER_WINDOW_DAYS, WHATSAPP_MAX_REMINDER_DAYS_BEFORE)
 
    query = (
        Enrollment.objects
        .filter(
            DueDate__isnull=False,
            DueDate__lte=today + timedelta(days=max_lookahead_days),
            DueDate__gte=today - timedelta(days=OVERDUE_CUTOFF_DAYS),
        )
        # CHANGED: no longer .exclude(last_expiry_notif_sent=today) here —
        # see architectural note. Per-enrollment gating for FCM/web push
        # happens inside the loop below via `already_notified_today`;
        # WhatsApp needs no such gate at all (its own dedup key covers it).
        .select_related('user', 'selectPlan', 'gym', 'gym__whatsapp_settings')
    )
 
    if gym:
        query = query.filter(gym=gym)
 
    enrollments = list(query)
 
    logger.info(
        "send_expiry_reminders: scope=%s candidates=%d",
        getattr(gym, 'gym_code', 'ALL_GYMS') if gym else 'ALL_GYMS',
        len(enrollments),
    )
 
    total_count = len(enrollments)
    marked_ids = set()
    buckets: dict[tuple, dict] = defaultdict(lambda: {'gym': None, 'enrollments': []})
 
    for enr in enrollments:
        try:
            days_left = (enr.DueDate - today).days
            gym_code  = getattr(enr.gym, 'gym_code', str(enr.gym_id))
 
            # Restores FCM/web push's EXACT original day range — the
            # query above is now wider (for WhatsApp's sake), but FCM and
            # web push must never act outside the range they always have.
            in_fcm_web_window = (-OVERDUE_CUTOFF_DAYS <= days_left <= REMINDER_WINDOW_DAYS)
            already_notified_today = (enr.last_expiry_notif_sent == today)
 
            ch1_ok = False
            ch2_ok = False
            if in_fcm_web_window and not already_notified_today:
                member_title, member_body = _build_member_message(enr, today)
                ch1_ok = _send_member_fcm(enr, member_title, member_body, gym_code)
                ch2_ok = _send_member_web_push(enr, member_title, member_body, gym_code)
 
            # WhatsApp is evaluated regardless of already_notified_today —
            # its own deduplication_key (per enrollment + DueDate +
            # direction) is the actual duplicate guard, not this flag.
            ch3_ok = _send_member_whatsapp_expiry(enr, days_left, gym_code)
 
            if ch1_ok or ch2_ok or ch3_ok:
                logger.info(
                    "Member notification succeeded enrollment=%s user=%s gym=%s "
                    "fcm=%s web=%s whatsapp=%s — persisting immediately",
                    enr.id, enr.user_id, gym_code, ch1_ok, ch2_ok, ch3_ok,
                )
                enr.last_expiry_notif_sent = today
                enr.save(update_fields=["last_expiry_notif_sent"])
                marked_ids.add(enr.id)
            elif in_fcm_web_window and not already_notified_today:
                logger.warning(
                    "Member notification failed on all channels "
                    "enrollment=%s user=%s gym=%s — will retry next run",
                    enr.id, enr.user_id, gym_code,
                )
 
            # Bucket for receptionist batching — FCM/web-push-only, so
            # only bucket enrollments within their original window,
            # exactly as before this feature.
            if in_fcm_web_window:
                key = (enr.gym_id, days_left)
                buckets[key]['gym'] = enr.gym
                buckets[key]['enrollments'].append(enr)
 
        except Exception:
            logger.exception(
                "Unexpected error processing enrollment=%s — skipping, "
                "continuing with remaining enrollments",
                getattr(enr, 'id', 'unknown'),
            )
            continue

    # ── Pass 2: batched receptionist notifications (unchanged batching) ───
    #
    # This stays exactly as before: one summary send per (gym, days_left)
    # bucket, not per member. A receptionist-notification failure is
    # isolated per bucket and never touches member notification status —
    # member rows were already committed in Pass 1.
    for (gym_id, days_left), bucket in buckets.items():
        try:
            bucket_gym  = bucket['gym']
            bucket_enrs = bucket['enrollments']
            gym_code    = getattr(bucket_gym, 'gym_code', str(gym_id))

            staff_title, staff_body = _build_staff_summary_message(
                bucket_enrs, days_left, bucket_gym
            )

            ch3_ok = _send_receptionist_fcm(bucket_gym, staff_title, staff_body, gym_code)
            ch4_ok = _send_receptionist_web_push(bucket_gym, staff_title, staff_body, gym_code)

            if ch3_ok or ch4_ok:
                # Credit every enrollment in this bucket that Pass 1 hadn't
                # already marked — staff were told about them even if the
                # member had no device. This bulk update is scoped to one
                # bucket (not the whole run) and happens right after this
                # bucket's send completes.
                unmarked_in_bucket = [
                    enr.id for enr in bucket_enrs if enr.id not in marked_ids
                ]
                if unmarked_in_bucket:
                    Enrollment.objects.filter(id__in=unmarked_in_bucket).update(
                        last_expiry_notif_sent=today
                    )
                    marked_ids.update(unmarked_in_bucket)

            logger.debug(
                "Staff batch gym=%s days_left=%d members=%d "
                "fcm=%s web=%s",
                gym_code, days_left, len(bucket_enrs), ch3_ok, ch4_ok,
            )

        except Exception:
            logger.exception(
                "Receptionist notification failed for gym=%s days_left=%d "
                "— member notification status for this bucket is unaffected, "
                "continuing with remaining buckets",
                gym_id, days_left,
            )
            continue

    failed_count = total_count - len(marked_ids)
    if failed_count:
        logger.warning(
            "send_expiry_reminders: %d enrollment(s) had ALL channels fail "
            "— will retry next cron run",
            failed_count,
        )

    logger.info(
        "Expiry reminders: scope=%s processed=%d notified=%d retrying=%d",
        getattr(gym, 'gym_code', 'ALL_GYMS') if gym else 'ALL_GYMS',
        total_count, len(marked_ids), failed_count,
    )
    return len(marked_ids)


# ── Public entry point — single-event member notification ─────────────────────

def notify_member_plan_changed(enrollment, new_plan,*,new_due_date=None,pending_amount=None) -> bool:
    """
    Member-facing 'your plan changed' push for a single enrollment. Fires
    the same two member channels the expiry-reminder job uses
    (_send_member_fcm / _send_member_web_push) below, just for one
    immediate event instead of a daily batch — so there's one
    implementation of "how do we push to a member" rather than two
    drifting copies.

    Called from billing/services/change_membership_plan.py after a plan
    change commits:
        from AuthFit.notifications import notify_member_plan_changed
        transaction.on_commit(lambda: notify_member_plan_changed(enrollment, new_plan))

    Returns True if at least one channel delivered.
    """
    if not enrollment.user_id:
        return False

    gym_code = getattr(enrollment.gym, 'gym_code', str(enrollment.gym_id))
    title = "Your membership plan has been updated."
    lines = [
        f"New Plan: {new_plan.plan}",
    ]
    if new_due_date:
        lines.append(
            f"Valid Until: {new_due_date:%d %b %Y}"
        )

    if pending_amount is not None:
        lines.append(
            f"Pending Amount: ₹{pending_amount}"
        )

    lines.append("Please check your profile.")
    body = "\n".join(lines)

    ch1_ok = _send_member_fcm(
        enrollment, title, body, gym_code,
        notif_type="plan_changed",
    )
    ch2_ok = _send_member_web_push(
        enrollment, title, body, gym_code,
        url="/profile/", notif_type="plan_changed",
    )

    ok = ch1_ok or ch2_ok
    if not ok:
        logger.warning(
            "notify_member_plan_changed: all member channels failed enrollment=%s",
            enrollment.id,
        )
    return ok

def notify_member_renewal_reminder(enrollment) -> bool:
    """
    Member-facing 'please renew, you scanned the QR while expired' push.
    Triggered manually by gym staff via the one-click 'Send Renewal
    Reminder' button on an AttendanceAttempt (reason='expired_plan').

    Reuses the same two member channels as notify_member_plan_changed
    and the expiry-reminder job — one implementation of "push to a
    member", not a third copy.
    """
    if not enrollment.user_id:
        return False

    gym_code = getattr(enrollment.gym, 'gym_code', str(enrollment.gym_id))
    member_name = enrollment.fullname or enrollment.user.get_full_name() or enrollment.user.username

    title = "Membership Renewal Reminder"
    body = (
        f"Hi {member_name}, your membership has expired. Please renew your "
        f"membership to continue marking attendance and accessing gym services."
    )

    ch1_ok = _send_member_fcm(
        enrollment, title, body, gym_code,
        notif_type="renewal_reminder",
    )
    ch2_ok = _send_member_web_push(
        enrollment, title, body, gym_code,
        url="/renew-membership/", notif_type="renewal_reminder",
    )

    ok = ch1_ok or ch2_ok
    if not ok:
        logger.warning(
            "notify_member_renewal_reminder: all member channels failed enrollment=%s",
            enrollment.id,
        )
    return ok
# ── Member channel implementations (shared by the expiry batch job and ───────
# ── single-event callers like notify_member_plan_changed above) ──────────────
# UNCHANGED from the existing implementation — reused as-is.

def _log_push_notification(enr, channel, notif_type, title, body, success, error=""):
    """
    Persists one push-send attempt to PushNotificationLog. Wrapped in its
    own try/except so a logging failure can NEVER take down the actual
    notification send path — mirrors the isolation philosophy used
    everywhere else in this file.
    """
    from Gym.models import PushNotificationLog
    try:
        PushNotificationLog.objects.create(
            gym=enr.gym,
            member=enr,
            channel=channel,
            notif_type=notif_type if notif_type in dict(PushNotificationLog.NOTIF_TYPE_CHOICES) else '',
            title=title,
            body=body,
            success=success,
            error=error[:2000],
        )
    except Exception:
        logger.exception(
            "_log_push_notification: failed to write log row enrollment=%s channel=%s",
            enr.id, channel,
        )


def _send_member_fcm(
    enr, title: str, body: str, gym_code: str,
    *, notif_type: str = 'plan_expiry', channel_id: str = 'entergym_expiry',
) -> bool:
    """
    Channel 1 — Member FCM via UserDevice.
    Scoped to user + gym. Returns True if ≥1 delivery succeeded.
    """
    try:
        tokens = list(
            UserDevice.objects
            .filter(user=enr.user, gym=enr.gym, active=True)
            .values_list('fcm_token', flat=True)
        )

        if not tokens:
            logger.debug(
                "_send_member_fcm: no active tokens "
                "enrollment=%s user=%s gym=%s",
                enr.id, enr.user_id, gym_code,
            )
            _log_push_notification(
                enr, 'fcm', notif_type, title, body,
                success=False, error="No active FCM tokens registered for this user.",
            )
            return False

        successes = send_push_to_tokens(
            tokens=tokens,
            title=title,
            body=body,
            data={
                "enrollment_id": str(enr.id),
                "gym_id":        str(enr.gym_id),
                "screen":        "Profile",
                "type":          notif_type,
            },
            channel_id=channel_id,
        )

        ok = successes > 0
        if ok:
            logger.info(
                "Expiry member FCM sent "
                "enrollment=%s user=%s gym=%s successes=%s",
                enr.id, enr.user_id, gym_code, successes,
            )
        _log_push_notification(
            enr, 'fcm', notif_type, title, body,
            success=ok, error="" if ok else "FCM send returned 0 successes.",
        )
        return ok

    except Exception as exc:
        logger.exception(
            "_send_member_fcm: failed enrollment=%s gym=%s",
            enr.id, gym_code,
        )
        _log_push_notification(
            enr, 'fcm', notif_type, title, body,
            success=False, error=str(exc),
        )
        return False


def _send_member_web_push(
    enr, title: str, body: str, gym_code: str,
    *, url: str = "/renew-membership/", notif_type: str = 'plan_expiry',
) -> bool:
    """
    Channel 2 — Member browser/PWA via WebPushSubscription.
    Returns True if ≥1 delivery succeeded.
    """
    try:
        successes = send_web_push(
            user=enr.user,
            title=title,
            body=body,
            url=url,
        )

        ok = successes > 0
        if ok:
            logger.info(
                "Expiry member web push sent "
                "enrollment=%s user=%s gym=%s successes=%s",
                enr.id, enr.user_id, gym_code, successes,
            )
        else:
            logger.debug(
                "_send_member_web_push: 0 deliveries "
                "enrollment=%s user=%s gym=%s",
                enr.id, enr.user_id, gym_code,
            )
        _log_push_notification(
            enr, 'web_push', notif_type, title, body,
            success=ok, error="" if ok else "Web push returned 0 successful deliveries (subscription may be expired).",
        )
        return ok

    except Exception as exc:
        logger.exception(
            "_send_member_web_push: failed enrollment=%s gym=%s",
            enr.id, gym_code,
        )
        _log_push_notification(
            enr, 'web_push', notif_type, title, body,
            success=False, error=str(exc),
        )
        return False


# ── Receptionist batch channel implementations ────────────────────────────────
# UNCHANGED from the existing implementation — reused as-is.

def _send_receptionist_fcm(
    gym, title: str, body: str, gym_code: str
) -> bool:
    """
    Channel 3 — Receptionist FCM via StaffDevice.
    Filtered to role='receptionist' only — gym_owner and trainer excluded.
    Sends ONE message per gym × days_left group regardless of member count.
    Returns True if ≥1 delivery succeeded.
    """
    from Shop.models import StaffDevice  # local import avoids circular import

    try:
        tokens = list(
            StaffDevice.objects
            .filter(
                gym=gym,
                active=True,
                user__staff_profile__role='receptionist',
            )
            .values_list('fcm_token', flat=True)
        )

        if not tokens:
            logger.debug(
                "_send_receptionist_fcm: no active receptionist tokens gym=%s",
                gym_code,
            )
            return False

        successes = send_push_to_tokens(
            tokens=tokens,
            title=title,
            body=body,
            data={
                "gym_id": str(gym.id),
                "url":    "/admin-tools/payments/",
                "type":   "plan_expiry_staff_alert",
            },
            channel_id='entergym_expiry',
        )

        if successes > 0:
            logger.info(
                "Expiry staff FCM sent gym=%s successes=%s",
                gym_code, successes,
            )
        return successes > 0

    except Exception:
        logger.exception(
            "_send_receptionist_fcm: failed gym=%s", gym_code,
        )
        return False


def _send_receptionist_web_push(
    gym, title: str, body: str, gym_code: str
) -> bool:
    """
    Channel 4 — Receptionist browser/PWA via WebPushSubscription.
    Filtered to role='receptionist' only via StaffProfile.
    Sends ONE summary message per gym × days_left group.
    Returns True if ≥1 delivery succeeded.
    """
    try:
        from Gym.models import StaffProfile

        receptionist_user_ids = (
            StaffProfile.objects
            .filter(gym=gym, active=True, role='receptionist')
            .values_list('user_id', flat=True)
        )

        receptionist_users = User.objects.filter(
            id__in=receptionist_user_ids,
            is_active=True,
        )

        total_successes = 0
        for user in receptionist_users:
            total_successes += send_web_push(
                user=user,
                title=title,
                body=body,
                url="/admin-tools/payments/",
            )

        if total_successes > 0:
            logger.info(
                "Expiry staff web push sent gym=%s successes=%s",
                gym_code, total_successes,
            )
        else:
            logger.debug(
                "_send_receptionist_web_push: 0 deliveries gym=%s",
                gym_code,
            )
        return total_successes > 0

    except Exception:
        logger.exception(
            "_send_receptionist_web_push: failed gym=%s", gym_code,
        )
        return False


# ── Message builders ──────────────────────────────────────────────────────────
# UNCHANGED from the existing implementation — reused as-is.

def _build_member_message(enr, today) -> tuple[str, str]:
    """
    Member-facing copy. First person — addresses the member directly.
    """
    days_left = (enr.DueDate - today).days
    plan_name = enr.selectPlan.plan if enr.selectPlan else "your plan"
    gym_name  = enr.gym.gym_name    if enr.gym        else "the gym"

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
            f"Your {plan_name} expires in {days_left} day(s). "
            f"Renew now to avoid losing access."
        )

    return title, body


def _build_staff_summary_message(
    enrollments: list, days_left: int, gym
) -> tuple[str, str]:
    """
    Receptionist-facing summary. Third person, batched.
    One message covers all expiring members in this gym × days_left group.
    """
    gym_name = gym.gym_name if gym else "the gym"
    count    = len(enrollments)

    # ── Time phrase ───────────────────────────────────────────────────────
    if days_left < 0:
        time_phrase = f"{abs(days_left)} day(s) ago"
    elif days_left == 0:
        time_phrase = "today"
    elif days_left == 1:
        time_phrase = "tomorrow"
    else:
        time_phrase = f"in {days_left} day(s)"

    # ── Title ─────────────────────────────────────────────────────────────
    if count == 1:
        if days_left < 0:
            title = f"Member Plan Expired — {gym_name}"
        elif days_left == 0:
            title = f"Member Plan Expires Today — {gym_name}"
        elif days_left == 1:
            title = f"Member Plan Expiring Tomorrow — {gym_name}"
        else:
            title = f"Member Plan Expiring Soon — {gym_name}"
    else:
        if days_left < 0:
            title = f"Member Plans Expired — {gym_name}"
        elif days_left <= 1:
            day_word = "Today" if days_left == 0 else "Tomorrow"
            title = f"Member Plans Expiring {day_word} — {gym_name}"
        elif count > 5:
            title = f"Membership Renewals Due — {gym_name}"
        else:
            title = f"Member Plans Expiring Soon — {gym_name}"

    # ── Member name list ──────────────────────────────────────────────────
    def first_name(enr):
        return (enr.fullname or enr.user.get_full_name() or enr.user.username).split()[0]

    names = [first_name(e) for e in enrollments]

    # ── Body ──────────────────────────────────────────────────────────────
    if count == 1:
        enr       = enrollments[0]
        plan_name = enr.selectPlan.plan if enr.selectPlan else "plan"
        body = f"{names[0]}'s {plan_name} expires {time_phrase}."

    elif count == 2:
        body = f"{names[0]} and {names[1]} expire {time_phrase}."

    elif count <= 5:
        body = f"{', '.join(names[:-1])} and {names[-1]} expire {time_phrase}."

    else:
        shown    = names[:2]
        overflow = count - 2
        body = f"{', '.join(shown)} and {overflow} others expire {time_phrase}."

    return title, body

def _send_member_whatsapp_expiry(enr, days_left: int, gym_code: str) -> bool:
    """
    Third member channel for expiry reminders. Now driven entirely by the
    gym's own configurable GymWhatsAppSettings instead of a hardcoded
    day-offset rule:
 
      - Pre-expiry: fires ONLY when days_left == wa_settings.reminder_days_before
        (exactly one day, not a range).
      - Post-expiry: fires ONLY when wa_settings.send_post_expiry_reminder
        is True AND days_left == -1 exactly (never on day -2 or earlier).
      - Time-of-day: fires ONLY when the CURRENT time in the gym's own
        configured timezone is within _REMINDER_TIME_TOLERANCE_MINUTES of
        wa_settings.reminder_time. This is what makes the 3x-daily cron
        model work — most invocations of send_expiry_reminders() will
        find this check False for most gyms/enrollments and simply skip.
 
    Still mirrors the try/except-isolated, return-False-on-any-failure
    shape of _send_member_fcm/_send_member_web_push exactly.
    """
    if not enr.user_id and not enr.phone:
        return False
 
    try:
        gym = enr.gym
        try:
            wa_settings = gym.whatsapp_settings
        except Exception:
            return False  # WhatsApp never configured for this gym — not an error
 
        if not wa_settings.is_operational:
            return False  # owner has WhatsApp turned off for this gym
 
        if not _reminder_time_matches(wa_settings):
            return False  # not this gym's configured send time yet — try again on a later run today
 
        member_name = enr.fullname or (enr.user.get_full_name() if enr.user_id else "") or "Member"
        to_phone = whatsapp_service.normalize_phone_to_e164(enr.phone)
 
        if days_left < 0:
            if not wa_settings.send_post_expiry_reminder:
                return False
            if days_left != -1:
                return False  # spec: send ONLY on day -1, never day -2 or earlier
            template_name = TEMPLATE_MEMBERSHIP_EXPIRY_AFTER
            components = build_expiry_after_components(
                member_name=member_name, gym_name=gym.gym_name, expiry_date=enr.DueDate,
            )
            dedup_key = f"whatsapp_expiry_after:{enr.id}:{enr.DueDate.isoformat()}"
        elif days_left == wa_settings.reminder_days_before:
            template_name = TEMPLATE_MEMBERSHIP_EXPIRY_BEFORE
            components = build_expiry_before_components(
                member_name=member_name, gym_name=gym.gym_name, expiry_date=enr.DueDate,
            )
            dedup_key = f"whatsapp_expiry_before:{enr.id}:{enr.DueDate.isoformat()}"
        else:
            return False  # not this gym's configured pre-expiry offset
 
        result = whatsapp_service.send_template(
            gym, to_phone, template_name,
            components=components, member=enr, deduplication_key=dedup_key,
        )
 
        if result.success:
            logger.info(
                "WhatsApp expiry reminder sent enrollment=%s gym=%s template=%s "
                "reminder_days_before=%s reminder_time=%s (skipped_duplicate=%s)",
                enr.id, gym_code, template_name,
                wa_settings.reminder_days_before, wa_settings.reminder_time,
                result.skipped_duplicate,
            )
        return result.success
 
    except Exception:
        logger.exception(
            "_send_member_whatsapp_expiry: failed enrollment=%s gym=%s",
            enr.id, gym_code,
        )
        return False

def _reminder_time_matches(wa_settings, now_utc=None) -> bool:
    """
    True when the CURRENT time, converted into this gym's own configured
    timezone, falls within _REMINDER_TIME_TOLERANCE_MINUTES of
    wa_settings.reminder_time. Falls back to Asia/Kolkata if the stored
    timezone string is somehow invalid (defensive — the form's
    TIMEZONE_CHOICES should prevent this, but a bad value must never
    crash the whole cron run for every other gym).
    """
    try:
        tz = ZoneInfo(wa_settings.timezone or "Asia/Kolkata")
    except Exception:
        logger.warning(
            "_reminder_time_matches: invalid timezone '%s' for gym=%s, falling back to Asia/Kolkata",
            wa_settings.timezone, wa_settings.gym_id,
        )
        tz = ZoneInfo("Asia/Kolkata")
 
    now_local = (now_utc or timezone.now()).astimezone(tz)
    now_minutes = now_local.hour * 60 + now_local.minute
    configured_minutes = wa_settings.reminder_time.hour * 60 + wa_settings.reminder_time.minute
 
    # Handle midnight wraparound (e.g. configured 23:50, now 00:05) just
    # in case a future timezone choice puts a slot near midnight —
    # none of today's three fixed options do, but this keeps the helper
    # correct rather than quietly wrong at the boundary.
    diff = abs(now_minutes - configured_minutes)
    diff = min(diff, 1440 - diff)
    return diff <= _REMINDER_TIME_TOLERANCE_MINUTES


def send_test_notification_to_member(enr) -> dict:
    """
    Manual 'Check Notification' button — staff-triggered test push to a
    single member on both channels. Reuses the exact same send path as
    real notifications (_send_member_fcm / _send_member_web_push), so a
    green result here means the real reminders will actually deliver.
    """
    from AuthFit.models import UserDevice
    from notifications.models import WebPushSubscription

    title = "Test Notification"
    body  = "This is a test — if you see this, notifications are working."
    gym_code = getattr(enr.gym, 'gym_code', str(enr.gym_id))

    # FCM
    has_fcm_device = enr.user_id and UserDevice.objects.filter(
        user=enr.user, gym=enr.gym, active=True
    ).exists()
    fcm_ok = False
    if has_fcm_device:
        fcm_ok = _send_member_fcm(enr, title, body, gym_code, notif_type='test')

    # Web Push
    has_web_sub = enr.user_id and WebPushSubscription.objects.filter(
        user=enr.user, active=True
    ).exists()
    web_ok = False
    if has_web_sub:
        web_ok = _send_member_web_push(enr, title, body, gym_code, notif_type='test')

    def status(has_device, sent_ok):
        if not has_device:
            return "no_device"      # nothing registered — not a failure, just nothing to test
        return "success" if sent_ok else "failed"

    return {
        "fcm": status(has_fcm_device, fcm_ok),
        "web_push": status(has_web_sub, web_ok),
    }
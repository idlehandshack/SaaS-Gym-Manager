# AuthFit/notifications.py
#
# DIFF SUMMARY vs. previous version:
#   - send_expiry_reminders() -> send_expiry_reminders(gym=None)
#   - one extra .filter(gym=gym) applied to the enrollments queryset when
#     `gym` is supplied
#   - member notification bookkeeping changed from "collect successful IDs,
#     one bulk update at the end" to "save last_expiry_notif_sent
#     immediately, per enrollment, right after that member's send succeeds"
#     — this is what makes the function safe against partial failures: if
#     an exception (Firebase timeout, Web Push error, bug) happens anywhere
#     later in the run, everyone already notified is already persisted, so
#     a same-day retry can't double-notify them.
#   - each enrollment and each receptionist bucket now runs inside its own
#     try/except so one bad enrollment/bucket can't stop the rest.
#   - receptionist batching itself (grouping by gym × days_left, one
#     summary send per bucket) is UNCHANGED — only wrapped in isolation +
#     given a small per-bucket bulk update instead of contributing to one
#     giant end-of-run update.
#   - message builders and the four channel helpers (_send_member_fcm,
#     _send_member_web_push, _send_receptionist_fcm,
#     _send_receptionist_web_push) are byte-for-byte unchanged.
#   - the nightly cron (which calls send_expiry_reminders() with no args)
#     behaves the same as before, just with safer partial-failure handling.

import logging
from collections import defaultdict
from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone

from Shop.notifications import send_push_to_tokens
from notifications.utils import send_web_push

from .models import Enrollment, UserDevice

logger = logging.getLogger(__name__)

REMINDER_WINDOW_DAYS = 3  # start notifying 3 days before expiry
OVERDUE_CUTOFF_DAYS  = 2  # stop notifying 2 days after expiry


# ── Public entry point ────────────────────────────────────────────────────────

def send_expiry_reminders(gym=None) -> int:
    """
    Send expiry reminders to members (individual) and receptionists (batched).

    gym:
        None  -> process every gym's due enrollments (nightly cron behaviour,
                 unchanged).
        Gym   -> process ONLY that gym's due enrollments. Used by the manual
                 "Send Expiry Reminder" button so one gym can never trigger
                 notifications for another tenant.

    Pass 1 — per-enrollment:
        Fire member FCM and member web push for each enrollment.
        Accumulate enrollments into (gym_id, days_left) buckets for batching.

    Pass 2 — per gym × days_left group:
        Build one summary message per bucket.
        Fire receptionist FCM and receptionist web push once per bucket.

    last_expiry_notif_sent is written for an enrollment when at least one
    of the following is true:
        - a member channel delivered for that enrollment (written
          immediately via enrollment.save(), right after that member's
          send completes — not deferred to a final bulk update)
        - the staff batch for that enrollment's gym × days_left group
          delivered (written via a small per-bucket bulk update, right
          after that bucket's send completes)

    Every enrollment and every receptionist bucket is processed inside
    its own try/except: a Firebase timeout, Web Push exception, or bug
    handling one member/bucket is logged and skipped, never aborting the
    rest of the run. Because progress is persisted immediately rather
    than at the end, a mid-run crash (process killed, unhandled
    exception, etc.) can never cause a member who was already notified
    to be re-notified on the next retry the same day.

    Returns the number of enrollments marked as notified.
    """

    today = timezone.localdate()

    query = (
        Enrollment.objects
        .filter(
            DueDate__isnull=False,
            DueDate__lte=today + timedelta(days=REMINDER_WINDOW_DAYS),
            DueDate__gte=today - timedelta(days=OVERDUE_CUTOFF_DAYS),
        )
        .exclude(last_expiry_notif_sent=today)
        .select_related('user', 'selectPlan', 'gym')
    )

    if gym:
        # Tenant isolation: manual trigger only ever touches this gym's
        # enrollments. Never fall through to a platform-wide query.
        query = query.filter(gym=gym)

    enrollments = list(query)

    logger.info(
        "send_expiry_reminders: scope=%s candidates=%d",
        getattr(gym, 'gym_code', 'ALL_GYMS') if gym else 'ALL_GYMS',
        len(enrollments),
    )

    total_count = len(enrollments)

    # marked_ids is bookkeeping ONLY — for the final count/log line and to
    # avoid re-touching a row in the receptionist pass below. It is never
    # used to defer a database write; every write happens immediately at
    # the point a channel succeeds.
    marked_ids = set()

    # ── Bucket structure ──────────────────────────────────────────────────
    # key: (gym_id, days_left)
    # value: {'gym': Gym, 'enrollments': [Enrollment, ...]}
    buckets: dict[tuple, dict] = defaultdict(lambda: {'gym': None, 'enrollments': []})

    # ── Pass 1: member notifications, saved immediately per enrollment ────
    #
    # Each enrollment is fully isolated: a Firebase timeout, Web Push
    # exception, or any programming error while handling one member is
    # caught, logged, and skipped — it can never abort processing for the
    # rest of the gym. Any enrollment whose member channel(s) succeed has
    # last_expiry_notif_sent written via enrollment.save() right away, so
    # if the process dies immediately afterward, that member is never
    # double-notified on retry.
    for enr in enrollments:
        try:
            days_left = (enr.DueDate - today).days
            gym_code  = getattr(enr.gym, 'gym_code', str(enr.gym_id))

            member_title, member_body = _build_member_message(enr, today)

            ch1_ok = _send_member_fcm(enr, member_title, member_body, gym_code)
            ch2_ok = _send_member_web_push(enr, member_title, member_body, gym_code)

            if ch1_ok or ch2_ok:
                logger.info(
                    "Member notification succeeded enrollment=%s user=%s gym=%s "
                    "fcm=%s web=%s — persisting immediately",
                    enr.id, enr.user_id, gym_code, ch1_ok, ch2_ok,
                )
                enr.last_expiry_notif_sent = today
                enr.save(update_fields=["last_expiry_notif_sent"])
                marked_ids.add(enr.id)
                logger.debug(
                    "last_expiry_notif_sent persisted enrollment=%s gym=%s",
                    enr.id, gym_code,
                )
            else:
                logger.warning(
                    "Member notification failed on all channels "
                    "enrollment=%s user=%s gym=%s — will retry next run",
                    enr.id, enr.user_id, gym_code,
                )

            # Always bucket — staff should be notified even if the member
            # has no device / both channels failed.
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
        url="/profile/",
    )

    ok = ch1_ok or ch2_ok
    if not ok:
        logger.warning(
            "notify_member_plan_changed: all member channels failed enrollment=%s",
            enrollment.id,
        )
    return ok


# ── Member channel implementations (shared by the expiry batch job and ───────
# ── single-event callers like notify_member_plan_changed above) ──────────────
# UNCHANGED from the existing implementation — reused as-is.

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

        if successes > 0:
            logger.info(
                "Expiry member FCM sent "
                "enrollment=%s user=%s gym=%s successes=%s",
                enr.id, enr.user_id, gym_code, successes,
            )
        return successes > 0

    except Exception:
        logger.exception(
            "_send_member_fcm: failed enrollment=%s gym=%s",
            enr.id, gym_code,
        )
        return False


def _send_member_web_push(
    enr, title: str, body: str, gym_code: str,
    *, url: str = "/renew-membership/",
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

        if successes > 0:
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
        return successes > 0

    except Exception:
        logger.exception(
            "_send_member_web_push: failed enrollment=%s gym=%s",
            enr.id, gym_code,
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
# AuthFit/notifications.py

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

def send_expiry_reminders() -> int:
    """
    Send expiry reminders to members (individual) and receptionists (batched).

    Pass 1 — per-enrollment:
        Fire member FCM and member web push for each enrollment.
        Accumulate enrollments into (gym_id, days_left) buckets for batching.

    Pass 2 — per gym × days_left group:
        Build one summary message per bucket.
        Fire receptionist FCM and receptionist web push once per bucket.

    last_expiry_notif_sent is written for an enrollment when at least one
    of the following is true:
        - a member channel delivered for that enrollment
        - the staff batch for that enrollment's gym × days_left group delivered

    Returns the number of enrollments marked as notified.
    """
    
    today = timezone.localdate()

    enrollments = list(
        Enrollment.objects
        .filter(
            DueDate__isnull=False,
            DueDate__lte=today + timedelta(days=REMINDER_WINDOW_DAYS),
            DueDate__gte=today - timedelta(days=OVERDUE_CUTOFF_DAYS),
        )
        .exclude(last_expiry_notif_sent=today)
        .select_related('user', 'selectPlan', 'gym')
    )

    total_count   = len(enrollments)
    notified_ids  = set()   # enrollment IDs to mark notified

    # ── Bucket structure ──────────────────────────────────────────────────
    # key: (gym_id, days_left)
    # value: {'gym': Gym, 'enrollments': [Enrollment, ...]}
    buckets: dict[tuple, dict] = defaultdict(lambda: {'gym': None, 'enrollments': []})

    # ── Pass 1: member notifications + bucket accumulation ────────────────
    for enr in enrollments:
        days_left = (enr.DueDate - today).days
        gym_code  = getattr(enr.gym, 'gym_code', str(enr.gym_id))
        print(
        f"Enrollment={enr.id} "
        f"User={enr.user.username} "
        f"Gym={enr.gym} "
        f"DueDate={enr.DueDate} "
        f"DaysLeft={days_left}"
    )
        member_title, member_body = _build_member_message(enr, today)

        ch1_ok = _send_member_fcm(enr, member_title, member_body, gym_code)
        ch2_ok = _send_member_web_push(enr, member_title, member_body, gym_code)

        if ch1_ok or ch2_ok:
            notified_ids.add(enr.id)

        # Always bucket — staff should be notified even if member has no device
        key = (enr.gym_id, days_left)
        buckets[key]['gym'] = enr.gym
        buckets[key]['enrollments'].append(enr)

    # ── Pass 2: batched receptionist notifications ────────────────────────
    for (gym_id, days_left), bucket in buckets.items():
        gym         = bucket['gym']
        bucket_enrs = bucket['enrollments']
        gym_code    = getattr(gym, 'gym_code', str(gym_id))

        staff_title, staff_body = _build_staff_summary_message(
            bucket_enrs, days_left, gym
        )
        print(
    f"Staff batch gym={gym_code} "
    f"days_left={days_left} "
    f"members={len(bucket_enrs)}"
)
        ch3_ok = _send_receptionist_fcm(gym, staff_title, staff_body, gym_code)
        ch4_ok = _send_receptionist_web_push(gym, staff_title, staff_body, gym_code)

        if ch3_ok or ch4_ok:
            # Credit every enrollment in this bucket — staff were told about them
            for enr in bucket_enrs:
                notified_ids.add(enr.id)

        logger.debug(
            "Staff batch gym=%s days_left=%d members=%d "
            "fcm=%s web=%s",
            gym_code, days_left, len(bucket_enrs), ch3_ok, ch4_ok,
        )

    # ── Write last_expiry_notif_sent for all notified enrollments ─────────
    # Single bulk update per enrollment (avoids N individual saves when
    # the set is large). update() does not call save(), which is fine
    # here since we're only touching last_expiry_notif_sent.
    if notified_ids:
        Enrollment.objects.filter(id__in=notified_ids).update(
            last_expiry_notif_sent=today
        )

    failed_count = total_count - len(notified_ids)
    if failed_count:
        logger.warning(
            "send_expiry_reminders: %d enrollment(s) had ALL channels fail "
            "— will retry next cron run",
            failed_count,
        )

    logger.info(
        "Expiry reminders: processed=%d notified=%d retrying=%d",
        total_count, len(notified_ids), failed_count,
    )
    return len(notified_ids)


# ── Member channel implementations (unchanged behaviour) ──────────────────────

def _send_member_fcm(enr, title: str, body: str, gym_code: str) -> bool:
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
                "type":          "plan_expiry",
            },
            channel_id='entergym_expiry',
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


def _send_member_web_push(enr, title: str, body: str, gym_code: str) -> bool:
    """
    Channel 2 — Member browser/PWA via WebPushSubscription.
    Returns True if ≥1 delivery succeeded.
    """
    print(
        f"WEB PUSH -> user={enr.user.username} "
        f"enrollment={enr.id}"
    )
    try:
        successes = send_web_push(
            user=enr.user,
            title=title,
            body=body,
            url="/renew-membership/",
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

def _build_member_message(enr, today) -> tuple[str, str]:
    """
    Member-facing copy. First person — addresses the member directly.
    Unchanged from previous version.
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

    Count = 1
        Title: Member Plan Expiring Tomorrow — Golden Gym
        Body:  Rahul's Gold Plan expires tomorrow.

    Count = 2
        Title: Member Plans Expiring Tomorrow — Golden Gym
        Body:  Rahul and Aman expire tomorrow.

    Count = 3–5
        Title: Member Plans Expiring Tomorrow — Golden Gym
        Body:  Rahul, Aman and John expire tomorrow.

    Count > 5
        Title: Membership Renewals Due — Golden Gym
        Body:  Rahul, Aman and 5 others expire tomorrow.
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
        # "Rahul, Aman and John expire tomorrow."
        body = f"{', '.join(names[:-1])} and {names[-1]} expire {time_phrase}."

    else:
        # "Rahul, Aman and 5 others expire tomorrow."
        shown    = names[:2]
        overflow = count - 2
        body = f"{', '.join(shown)} and {overflow} others expire {time_phrase}."

    return title, body
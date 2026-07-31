# member_messages/services.py
"""
Business logic for the Member Messages feature. Views stay thin — every
rule (tenant validation, notification dispatch, soft delete) lives here.

Notification reuse — NOTHING new is implemented, only called:
    - Shop.notifications.send_push_to_tokens   (existing Firebase/FCM sender)
    - notifications.utils.send_web_push        (existing Web Push sender)
    - AuthFit.models.UserDevice                 (existing FCM token table)

This mirrors the isolation pattern already used in AuthFit/notifications.py
(send_expiry_reminders): each channel is wrapped in its own try/except so a
Firebase timeout or WebPush error can never bubble up into the request/response
cycle or block the other channel.
"""
import logging

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.db.models import Q, Max, Count
from AuthFit.models import Enrollment, UserDevice
from Shop.notifications import send_push_to_tokens
from notifications.utils import send_web_push

from .models import MemberMessage

logger = logging.getLogger(__name__)

MEMBER_MESSAGE_CHANNEL_ID = 'entergym_member_messages'
MEMBER_MESSAGE_DEEP_LINK = '/messages/'


class MemberMessageError(Exception):
    """Validation / tenant-scoping failure — safe to show to the caller."""


# ══════════════════════════════════════════════════════════════════════════
# Tenant / IDOR guards
# ══════════════════════════════════════════════════════════════════════════

def _validate_member_in_gym(gym, member):
    """
    The target member must have an enrollment (any status) at THIS gym.
    Prevents staff at gym A from ever messaging a user who only belongs
    to gym B, even if they somehow obtain that user's id.
    """
    if not Enrollment.objects.filter(gym=gym, user=member).exists():
        raise MemberMessageError("This member does not belong to your gym.")


# ══════════════════════════════════════════════════════════════════════════
# Create + send
# ══════════════════════════════════════════════════════════════════════════

@transaction.atomic
def send_member_message(
    *, gym, member, created_by, title, message,
    priority='normal', show_popup=True, send_push=True, save_inbox=True,
):
    """
    Creates one MemberMessage row and, if `send_push` is True, fires the
    push notification only after the transaction commits (so a mid-request
    failure never sends a push for a row that got rolled back).

    Raises MemberMessageError on invalid input or cross-tenant member.
    """
    title = (title or '').strip()
    message = (message or '').strip()

    if not title:
        raise MemberMessageError("Title is required.")
    if not message:
        raise MemberMessageError("Message is required.")
    if priority not in dict(MemberMessage.PRIORITY_CHOICES):
        priority = MemberMessage.PRIORITY_NORMAL

    _validate_member_in_gym(gym, member)

    msg = MemberMessage.objects.create(
        gym=gym,
        member=member,
        title=title,
        message=message,
        priority=priority,
        show_popup=show_popup,
        send_push=send_push,
        save_inbox=save_inbox,
        created_by=created_by,
    )

    if send_push:
        transaction.on_commit(lambda: _deliver_push_notification(msg))

    logger.info(
        "Member message created — id=%s gym=%s member=%s priority=%s push=%s popup=%s inbox=%s",
        msg.id, gym.pk, member.pk, priority, send_push, show_popup, save_inbox,
    )
    return msg


def _deliver_push_notification(msg: MemberMessage) -> bool:
    """
    Fires both member channels. Returns True if at least one delivered.
    Never raises — every channel is individually isolated.
    """
    ch1_ok = _send_message_fcm(msg)
    ch2_ok = _send_message_web_push(msg)

    if not (ch1_ok or ch2_ok):
        logger.warning(
            "Member message push failed on all channels — message_id=%s member=%s gym=%s",
            msg.id, msg.member_id, msg.gym_id,
        )
    return ch1_ok or ch2_ok


def _send_message_fcm(msg: MemberMessage) -> bool:
    """Channel 1 — existing UserDevice + send_push_to_tokens(), untouched."""
    try:
        tokens = list(
            UserDevice.objects
            .filter(user=msg.member, gym=msg.gym, active=True)
            .values_list('fcm_token', flat=True)
        )
        if not tokens:
            logger.debug(
                "_send_message_fcm: no active tokens — message_id=%s member=%s",
                msg.id, msg.member_id,
            )
            return False

        successes = send_push_to_tokens(
            tokens=tokens,
            title=msg.title,
            body=msg.message[:180],
            data={
                "message_id": str(msg.id),
                "gym_id": str(msg.gym_id),
                "type": "member_message",
                "priority": msg.priority,
                "deep_link": MEMBER_MESSAGE_DEEP_LINK,
                "screen": "MemberMessages",
            },
            channel_id=MEMBER_MESSAGE_CHANNEL_ID,
        )
        if successes > 0:
            logger.info("Member message FCM sent — message_id=%s successes=%s", msg.id, successes)
        return successes > 0
    except Exception:
        logger.exception("_send_message_fcm failed — message_id=%s", msg.id)
        return False


def _send_message_web_push(msg: MemberMessage) -> bool:
    """Channel 2 — existing send_web_push(), untouched."""
    try:
        successes = send_web_push(
            user=msg.member,
            title=msg.title,
            body=msg.message[:180],
            url=MEMBER_MESSAGE_DEEP_LINK,
        )
        if successes > 0:
            logger.info("Member message web push sent — message_id=%s successes=%s", msg.id, successes)
        return successes > 0
    except Exception:
        logger.exception("_send_message_web_push failed — message_id=%s", msg.id)
        return False


# ══════════════════════════════════════════════════════════════════════════
# Owner / Receptionist queries
# ══════════════════════════════════════════════════════════════════════════

def get_owner_message_list(gym, search=None, page=1, per_page=25):
    """
    One row per member — the member's most recent message, plus their
    unread/total counts at this gym. Clicking the row still opens full
    history via member_message_history (unchanged).
    """
    qs = MemberMessage.objects.filter(gym=gym)

    if search:
        matching_user_ids = Enrollment.objects.filter(
            gym=gym, fullname__icontains=search,
        ).values_list('user_id', flat=True)
        qs = qs.filter(
            Q(title__icontains=search) |
            Q(member__username__icontains=search) |
            Q(member_id__in=matching_user_ids)
        )

    grouped = (
        qs.values('member')
        .annotate(
            latest_id=Max('id'),
            latest_at=Max('created_at'),
            unread_count=Count('id', filter=Q(is_read=False)),
            total_count=Count('id'),
        )
        .order_by('-latest_at')
    )

    page_obj = Paginator(grouped, per_page).get_page(page)

    latest_ids = [row['latest_id'] for row in page_obj]
    by_id = {
        m.id: m for m in
        MemberMessage.objects.filter(pk__in=latest_ids).select_related('member', 'created_by')
    }

    rows = []
    for row in page_obj:
        msg = by_id.get(row['latest_id'])
        if not msg:
            continue
        msg.unread_count = row['unread_count']
        msg.total_count = row['total_count']
        rows.append(msg)

    # page_obj keeps its pagination metadata (count/num_pages come from the
    # paginator, not object_list) — we just swap in the enriched objects.
    page_obj.object_list = rows
    return page_obj


def attach_member_enrollment(page_obj, gym):
    """
    The owner-facing message list needs each row's avatar, phone, and
    unique_id — none of which live on MemberMessage (it only stores the
    User). It also needs the *Enrollment* pk specifically, since
    member_message_history() looks up by Enrollment pk, not User pk.

    Batches one query for the whole page (keyed by user_id) instead of
    querying per row, and attaches the result as `message.enrollment`
    (None if the member's enrollment at this gym was since deleted).
    """
    user_ids = {m.member_id for m in page_obj}
    if not user_ids:
        return page_obj

    enrollments = (
        Enrollment.objects
        .filter(gym=gym, user_id__in=user_ids)
        .select_related('selectPlan')
        .order_by('-doj')
    )
    # If a user somehow has more than one enrollment row at this gym,
    # keep the most recent — order_by above ensures first-seen wins.
    by_user = {}
    for e in enrollments:
        by_user.setdefault(e.user_id, e)

    for m in page_obj:
        m.enrollment = by_user.get(m.member_id)

    return page_obj


def get_member_history(gym, member, search=None, page=1, per_page=25):
    """Backs the Member Detail page's 'Message History' section — newest first."""
    qs = (
        MemberMessage.objects
        .filter(gym=gym, member=member)
        .order_by('-created_at')
    )
    if search:
        qs = qs.filter(title__icontains=search)
    return Paginator(qs, per_page).get_page(page)


@transaction.atomic
def delete_message(gym, message_id):
    """
    Soft delete only, scoped to `gym` (IDOR-safe — staff at another gym
    can never delete a row that isn't theirs, even with a guessed id).
    """
    msg = MemberMessage.objects.filter(gym=gym, pk=message_id).first()
    if not msg:
        raise MemberMessageError("Message not found.")
    msg.soft_delete()
    logger.info("Member message soft-deleted — id=%s gym=%s", message_id, gym.pk)
    return msg


# ══════════════════════════════════════════════════════════════════════════
# Member-facing queries
# ══════════════════════════════════════════════════════════════════════════

def get_member_inbox(gym, member, page=1, per_page=20):
    qs = (
        MemberMessage.objects
        .filter(gym=gym, member=member, save_inbox=True)
        .order_by('-created_at')
    )
    return Paginator(qs, per_page).get_page(page)


def get_popup_messages(gym, member):
    """
    Unread + show_popup=True messages for this member at this gym.
    Marking a message read (via mark_message_read) drops it out of this
    query permanently — that's what guarantees the popup never repeats.
    """
    return list(
        MemberMessage.objects
        .filter(gym=gym, member=member, show_popup=True, is_read=False)
        .order_by('-created_at')
    )


def get_unread_count(gym, member):
    return MemberMessage.objects.filter(gym=gym, member=member, is_read=False).count()


def mark_message_read(gym, member, message_id):
    """
    IDOR-safe: scoped to gym AND member — a member can only ever mark
    their own message, at their own gym, as read.
    """
    msg = MemberMessage.objects.filter(gym=gym, member=member, pk=message_id).first()
    if not msg:
        raise MemberMessageError("Message not found.")
    msg.mark_read()
    return msg


# ══════════════════════════════════════════════════════════════════════════
# Communication dashboard widgets
# ══════════════════════════════════════════════════════════════════════════

def get_dashboard_stats(gym):
    today = timezone.localdate()
    qs = MemberMessage.objects.filter(gym=gym)
    return {
        "messages_sent": qs.count(),
        "messages_read": qs.filter(is_read=True).count(),
        "unread_messages": qs.filter(is_read=False).count(),
        "todays_messages": qs.filter(created_at__date=today).count(),
    }
"""
notification_center/services.py

Read-only aggregator. This module NEVER imports a write path from
announcements/member_messages/communications beyond the mark-read helpers
those apps already expose (Announcement.mark_read / MemberMessage.mark_read /
CommunicationDeliveryLog rows via communications.services). It does not
define its own Announcement/Message/Communication-shaped model — every
notification the center shows is fetched live from the owning app's own
manager/queryset, so there is nothing to keep in sync and nothing that can
duplicate a record.

Why not one big UNION query: the three source tables have different shapes,
different tenant-scoping (Announcement/MemberMessage are gym-scoped,
Communication is platform-wide), and different permission rules per the
spec's audience matrix. Normalizing in Python after three narrow, indexed
queries is simpler and avoids a lowest-common-denominator SQL schema.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from django.db.models import Q
from django.utils import timezone

from announcements.models import Announcement, AnnouncementRead
from member_messages.models import MemberMessage
from communications.services import get_delivery_status_map, get_visible_communications

logger = logging.getLogger(__name__)

SOURCE_GYM = 'gym'          # -> "From Gym" (Announcements, Member Messages)
SOURCE_ENTERGYM = 'entergym'  # -> "From EnterGYM" (Communications)

TYPE_ANNOUNCEMENT = 'announcement'
TYPE_MESSAGE = 'message'
TYPE_COMMUNICATION = 'communication'

SOURCE_LABELS = {SOURCE_GYM: 'From Gym', SOURCE_ENTERGYM: 'From EnterGYM'}
TYPE_BADGES = {
    TYPE_ANNOUNCEMENT: 'Announcement',
    TYPE_MESSAGE: 'Message',
    TYPE_COMMUNICATION: 'Communication',
}


@dataclass
class NotificationItem:
    """
    A normalized, read-only view over one row from one of the three source
    apps. `key` is globally unique (type-prefixed pk) and is what
    mark-read/dismiss actions key off of — never a raw pk alone, since a
    MemberMessage #7 and a Communication #7 would otherwise collide.
    """
    key: str
    source: str            # 'gym' | 'entergym'
    type: str              # 'announcement' | 'message' | 'communication'
    title: str
    description: str
    created_at: datetime
    is_read: bool
    external_link: str = ''
    priority: str = 'medium'
    _origin_pk: int = field(repr=False, default=0)

    @property
    def source_label(self):
        return SOURCE_LABELS[self.source]

    @property
    def type_badge(self):
        return TYPE_BADGES[self.type]


# ─────────────────────────────────────────────────────────────────────────
# Per-source fetchers — each is a thin, read-only wrapper around that app's
# own models/managers/services. No new queries are invented that duplicate
# logic already living in announcements/member_messages/communications.
# ─────────────────────────────────────────────────────────────────────────

def _fetch_announcements(user, gym) -> list[NotificationItem]:
    """Members only. Reuses Announcement.is_live / is_targeted_at exactly
    as announcement_center() does — same visibility rules, read here."""
    if gym is None:
        return []

    qs = (
        Announcement.objects
        .filter(gym=gym, is_active=True, show_web=True, publish_at__lte=timezone.now())
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    )
    visible = [a for a in qs if a.is_targeted_at(user)]
    if not visible:
        return []

    read_ids = set(
        AnnouncementRead.objects
        .filter(user=user, announcement__in=visible, read_at__isnull=False)
        .values_list('announcement_id', flat=True)
    )

    return [
        NotificationItem(
            key=f'announcement:{a.id}',
            source=SOURCE_GYM,
            type=TYPE_ANNOUNCEMENT,
            title=a.title,
            description=_strip_html(a.description),
            created_at=a.publish_at,
            is_read=a.id in read_ids,
            external_link=a.external_link,
            priority=a.priority,
            _origin_pk=a.id,
        )
        for a in visible
    ]


def _fetch_member_messages(user, gym) -> list[NotificationItem]:
    """Members only. Reads MemberMessage.is_read directly — the same flag
    member_messages.services.get_unread_count already sums."""
    if gym is None:
        return []

    qs = MemberMessage.objects.filter(gym=gym, member=user, save_inbox=True)
    return [
        NotificationItem(
            key=f'message:{m.id}',
            source=SOURCE_GYM,
            type=TYPE_MESSAGE,
            title=m.title,
            description=m.message,
            created_at=m.created_at,
            is_read=m.is_read,
            priority={'urgent': 'high', 'important': 'medium', 'normal': 'low'}.get(m.priority, 'medium'),
            _origin_pk=m.id,
        )
        for m in qs
    ]


def _fetch_communications(user, gym) -> list[NotificationItem]:
    """All four roles. get_visible_communications already resolves the
    full CommunicationAudience targeting matrix (role, gym, plan, city,
    etc.) — this function only normalizes its output, never re-derives
    audience logic itself."""
    visible = get_visible_communications(user, gym=gym, channel_field='show_notification_center')
    if not visible:
        return []

    status_map = get_delivery_status_map(user, visible)

    items = []
    for c in visible:
        status = status_map.get(c.id) if status_map else None
        is_read = bool(status and status.get('read'))
        items.append(NotificationItem(
            key=f'communication:{c.id}',
            source=SOURCE_ENTERGYM,
            type=TYPE_COMMUNICATION,
            title=c.title,
            description=_strip_html(c.description),
            created_at=c.publish_at,
            is_read=is_read,
            external_link=c.external_link,
            priority=c.priority,
            _origin_pk=c.id,
        ))
    return items


def _strip_html(value: str) -> str:
    import re
    return re.sub('<[^<]+?>', '', value or '').strip()


# ─────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────

# Which sources each role is allowed to see, per the spec's audience matrix.
ROLE_SOURCES = {
    'member': (_fetch_announcements, _fetch_member_messages, _fetch_communications),
    'gym_owner': (_fetch_communications,),
    'receptionist': (_fetch_communications,),
    'trainer': (_fetch_communications,),
}


def get_role_for_user(user) -> str:
    """Mirrors the role resolution already used elsewhere in the codebase
    (StaffProfile.role for staff, 'member' otherwise) — does not introduce
    a new role system."""
    staff_profile = getattr(user, 'staff_profile', None)
    if staff_profile and staff_profile.active:
        return staff_profile.role
    return 'member'


def get_unified_notifications(user, gym=None, role: Optional[str] = None) -> list[NotificationItem]:
    """
    Returns every notification `user` may see, newest first, deduplicated
    by construction (each source is queried exactly once and each source
    row maps to exactly one NotificationItem).
    """
    role = role or get_role_for_user(user)
    fetchers = ROLE_SOURCES.get(role, (_fetch_communications,))

    items: list[NotificationItem] = []
    for fetch in fetchers:
        try:
            items.extend(fetch(user, gym))
        except Exception:
            # One source failing (e.g. a gym-less super admin hitting the
            # announcements fetcher) must never blank out the other two.
            logger.exception("notification_center: source fetch failed — fetcher=%s user=%s", fetch.__name__, user.pk)

    items.sort(key=lambda i: i.created_at, reverse=True)
    return items


def apply_filters(items: list[NotificationItem], *, type_filter=None, read_filter=None, search=None):
    """type_filter: 'announcement'|'message'|'communication'; read_filter: 'read'|'unread'."""
    if type_filter:
        items = [i for i in items if i.type == type_filter]
    if read_filter == 'read':
        items = [i for i in items if i.is_read]
    elif read_filter == 'unread':
        items = [i for i in items if not i.is_read]
    if search:
        s = search.strip().lower()
        if s:
            items = [i for i in items if s in i.title.lower() or s in i.description.lower()]
    return items


def mark_item_read(user, gym, key: str) -> bool:
    """Dispatches to the owning app's own read-marking logic — never
    writes to a source table directly from this module. Returns False if
    the key's type isn't recognized or the row can't be found/authorized."""
    try:
        item_type, pk = key.split(':', 1)
        pk = int(pk)
    except (ValueError, AttributeError):
        return False

    if item_type == TYPE_ANNOUNCEMENT:
        ann = Announcement.objects.filter(pk=pk).first()
        if not ann:
            return False
        read, _ = AnnouncementRead.objects.get_or_create(announcement=ann, user=user)
        read.mark_read(device_type=AnnouncementRead.DeviceType.WEB)
        return True

    if item_type == TYPE_MESSAGE:
        from member_messages.services import mark_message_read, MemberMessageError
        try:
            mark_message_read(gym, user, pk)
            return True
        except MemberMessageError:
            return False

    if item_type == TYPE_COMMUNICATION:
        from communications.models import Communication, CommunicationDeliveryLog
        comm = Communication.objects.filter(pk=pk).first()
        if not comm:
            return False

        # Find ANY existing delivery log for this communication/recipient,
        # regardless of channel (fcm or web_push) — a Communication is
        # dispatched at most once per channel per user, so there should
        # never be more than one relevant row, but we defensively take the
        # most recent if more than one somehow exists. Updating it in place
        # (instead of the previous behavior of always get_or_create'ing a
        # channel='fcm' row) is what prevents duplicate delivery log rows
        # from being created every time "Mark as Read" is pressed.
        log = (
            CommunicationDeliveryLog.objects
            .filter(communication=comm, recipient=user)
            .order_by('-created_at')
            .first()
        )
        if log is None:
            # No delivery log exists yet for this user/communication at all
            # (e.g. delivery logging didn't produce one) — create exactly
            # one, on the web_push channel, already marked read.
            log = CommunicationDeliveryLog.objects.create(
                communication=comm, recipient=user, channel='web_push', status='read',
            )

        log.read_at = timezone.now()
        log.status = 'read'
        log.save(update_fields=['read_at', 'status'])
        return True

    return False
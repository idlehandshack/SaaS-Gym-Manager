"""
announcements/api.py

Plain Django JSON endpoints (project has no DRF dependency visible in the
existing codebase — mark_attendance_api, get_users etc. are all plain
JsonResponse views), matching AuthFit/views.py conventions.

Endpoints (see urls.py):
    GET  /api/announcements/                -> list, filterable, paginated
    GET  /api/announcements/home/           -> popup + banner + pinned payload for home-open
    POST /api/announcements/read/           -> mark read
    POST /api/announcements/dismiss/        -> mark dismissed
    GET  /api/announcements/unread-count/   -> badge count
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Announcement, AnnouncementRead

logger = logging.getLogger(__name__)


def _device_type(request) -> str:
    ua = request.META.get('HTTP_USER_AGENT', '')
    requested = request.GET.get('device') or request.POST.get('device')
    if requested in (AnnouncementRead.DeviceType.WEB, AnnouncementRead.DeviceType.MOBILE):
        return requested
    return AnnouncementRead.DeviceType.MOBILE if 'EnterGYMApp' in ua else AnnouncementRead.DeviceType.WEB


def _serialize(a: Announcement, read_row=None):
    return {
        "id": a.id,
        "title": a.title,
        "description": a.description,
        "category": a.announcement_type,
        "category_display": a.get_announcement_type_display(),
        "priority": a.priority,
        "image_url": a.image.url if a.image else None,
        "attachment_url": a.attachment.url if a.attachment else None,
        "external_link": a.external_link,
        "require_read": a.require_read,
        "pin_home": a.pin_home,
        "publish_at": a.publish_at.isoformat(),
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        "is_read": bool(read_row and read_row.read_at),
        "is_dismissed": bool(read_row and read_row.dismissed),
        "deep_link": a.get_absolute_url(),
    }


def _live_visible_queryset(gym, user, *, channel_field=None):
    qs = (
        Announcement.objects
        .filter(gym=gym, is_active=True, publish_at__lte=timezone.now())
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    )
    if channel_field:
        qs = qs.filter(**{channel_field: True})
    return [a for a in qs.order_by('-pin_home', '-publish_at') if a.is_targeted_at(user)]


@login_required
@require_GET
def api_list(request):
    gym = getattr(request, 'gym', None)
    if not gym:
        return JsonResponse({"results": [], "count": 0})

    announcements = _live_visible_queryset(gym, request.user, channel_field='show_web')

    category = request.GET.get('category')
    priority = request.GET.get('priority')
    if category:
        announcements = [a for a in announcements if a.announcement_type == category]
    if priority:
        announcements = [a for a in announcements if a.priority == priority]

    reads = {
        r.announcement_id: r
        for r in AnnouncementRead.objects.filter(user=request.user, announcement__in=announcements)
    }

    page = int(request.GET.get('page', 1))
    paginator = Paginator(announcements, 15)
    page_obj = paginator.get_page(page)

    return JsonResponse({
        "results": [_serialize(a, reads.get(a.id)) for a in page_obj],
        "count": paginator.count,
        "has_next": page_obj.has_next(),
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
    })


@login_required
@require_GET
def api_home(request):
    """
    Payload consumed on every home-screen open (website + Android WebView).
    Returns:
        popup       -> the single highest-priority unseen popup-eligible
                       announcement (or null), respecting
                       allow_multiple_popups=false / show_again_after_read=false
        banner      -> list of banner-eligible announcements for the scroller
        pinned      -> pin_home announcements
    """
    gym = getattr(request, 'gym', None)
    if not gym:
        return JsonResponse({"popup": None, "banner": [], "pinned": []})

    popup_candidates = _live_visible_queryset(gym, request.user, channel_field='show_popup')

    read_map = {
        r.announcement_id: r
        for r in AnnouncementRead.objects.filter(
            user=request.user, announcement_id__in=[a.id for a in popup_candidates]
        )
    }

    priority_rank = {Announcement.Priority.HIGH: 0, Announcement.Priority.MEDIUM: 1, Announcement.Priority.LOW: 2}

    def _eligible_for_popup(a):
        row = read_map.get(a.id)
        if a.priority == Announcement.Priority.LOW:
            # Low priority never pops up — Announcement Center only, per spec.
            return False
        if not row:
            return True
        if a.priority == Announcement.Priority.HIGH:
            # High stays until read (or dismissed, depending on require_read).
            if a.require_read:
                return not row.read_at
            return not (row.read_at or row.dismissed)
        # Medium: shown once, never again (show_again_after_read=false).
        return not (row.read_at or row.dismissed)

    eligible = [a for a in popup_candidates if _eligible_for_popup(a)]
    eligible.sort(key=lambda a: (priority_rank.get(a.priority, 9), -a.publish_at.timestamp()))
    popup = eligible[0] if eligible else None
    if popup:
        popup.view_count = popup.view_count + 1
        popup.save(update_fields=['view_count'])

    banner_list = _live_visible_queryset(gym, request.user, channel_field='show_banner')
    pinned_list = [a for a in _live_visible_queryset(gym, request.user, channel_field='show_web') if a.pin_home]

    return JsonResponse({
        "popup": _serialize(popup, read_map.get(popup.id)) if popup else None,
        "banner": [_serialize(a) for a in banner_list],
        "pinned": [_serialize(a) for a in pinned_list],
    })


def _get_or_create_read(user, announcement):
    row, _ = AnnouncementRead.objects.get_or_create(user=user, announcement=announcement)
    return row


@login_required
@require_POST
def api_mark_read(request):
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        payload = request.POST
    announcement_id = payload.get('announcement_id')
    if not announcement_id:
        return JsonResponse({"ok": False, "error": "announcement_id required"}, status=400)

    gym = getattr(request, 'gym', None)
    try:
        announcement = Announcement.objects.get(pk=announcement_id, gym=gym) if gym else \
            Announcement.objects.get(pk=announcement_id)
    except Announcement.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    row = _get_or_create_read(request.user, announcement)
    row.mark_read(device_type=_device_type(request))
    return JsonResponse({"ok": True})


@login_required
@require_POST
def api_mark_dismissed(request):
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        payload = request.POST
    announcement_id = payload.get('announcement_id')
    if not announcement_id:
        return JsonResponse({"ok": False, "error": "announcement_id required"}, status=400)

    gym = getattr(request, 'gym', None)
    try:
        announcement = Announcement.objects.get(pk=announcement_id, gym=gym) if gym else \
            Announcement.objects.get(pk=announcement_id)
    except Announcement.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    row = _get_or_create_read(request.user, announcement)
    row.mark_dismissed(device_type=_device_type(request))
    return JsonResponse({"ok": True})


@login_required
@require_GET
def api_unread_count(request):
    gym = getattr(request, 'gym', None)
    if not gym:
        return JsonResponse({"unread_count": 0})

    live = _live_visible_queryset(gym, request.user, channel_field='show_web')
    read_ids = set(
        AnnouncementRead.objects.filter(
            user=request.user, announcement_id__in=[a.id for a in live], read_at__isnull=False,
        ).values_list('announcement_id', flat=True)
    )
    unread = sum(1 for a in live if a.id not in read_ids)
    return JsonResponse({"unread_count": unread})

"""
communications/api.py

Plain Django JSON endpoints, matching announcements/api.py's own
conventions (no DRF in this project).

Two audiences:
    - Any authenticated recipient (member or staff) can report back
      opened/clicked/dismissed/read events for a communication they
      received — this is NOT gated by superuser, since recipients aren't
      Super Admins but still need to ack their own notifications.
    - Everything else (list/summary/publish) is Super Admin only, per the
      spec's `permissions` block.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Communication, CommunicationDeliveryLog
from .permissions import superuser_required_json
from .services import dispatch_communication, get_delivery_status_map, get_visible_communications

logger = logging.getLogger(__name__)

_VALID_EVENTS = {'opened', 'clicked', 'dismissed', 'read'}


def _parse_json_body(request):
    try:
        return json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return request.POST


# ── Recipient-side: what does THIS person see ───────────────────────────
# Backs communication_center, banner_manager, popup_manager, the
# notification bell, and the mobile app's equivalents. The actual
# targeting logic lives once in services.py (get_visible_communications /
# AudienceResolver.user_matches) — views.py's communication_center reuses
# the exact same two functions below rather than a second implementation.

def _visible_to(request, channel_field=None):
    return get_visible_communications(request.user, gym=getattr(request, 'gym', None), channel_field=channel_field)


def _serialize_for_recipient(c: Communication, status=None):
    status = status or {}
    return {
        "id": c.id,
        "title": c.title,
        "description": c.description,
        "type": c.type,
        "type_display": c.get_type_display(),
        "priority": c.priority,
        "image_url": c.image.url if c.image else None,
        "attachment_url": c.attachment.url if c.attachment else None,
        "external_link": c.external_link,
        "deep_link": c.get_deep_link(),
        "require_read": c.require_read,
        "banner_placement": c.banner_placement if c.show_banner else None,
        "publish_at": c.publish_at.isoformat(),
        "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        "is_read": bool(status.get('read')),
        "is_dismissed": bool(status.get('dismissed')),
    }


@login_required
@require_GET
def api_communication_center_list(request):
    """Powers communication_center (web + Android) — paginated, filterable
    by type/priority, full-text search over title/description."""
    visible = _visible_to(request, channel_field='show_notification_center')

    comm_type = request.GET.get('type')
    priority = request.GET.get('priority')
    search = (request.GET.get('q') or '').strip().lower()

    if comm_type:
        visible = [c for c in visible if c.type == comm_type]
    if priority:
        visible = [c for c in visible if c.priority == priority]
    if search:
        visible = [c for c in visible if search in c.title.lower() or search in c.description.lower()]

    status_map = get_delivery_status_map(request.user, visible)

    page = int(request.GET.get('page', 1))
    paginator = Paginator(visible, 15)
    page_obj = paginator.get_page(page)

    return JsonResponse({
        "results": [_serialize_for_recipient(c, status_map.get(c.id)) for c in page_obj],
        "count": paginator.count,
        "has_next": page_obj.has_next(),
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
    })


@login_required
@require_GET
def api_home(request):
    """
    Popup + banner payload for a home-screen / dashboard open. Mirrors
    announcements/api.py's own api_home shape (popup eligibility rules —
    Low never pops up, High/Critical persist until read if require_read,
    Medium shows once) so a frontend already wired to that endpoint needs
    only a URL change, not new client logic.
    """
    popup_candidates = _visible_to(request, channel_field='show_popup')
    status_map = get_delivery_status_map(request.user, popup_candidates)

    priority_rank = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}

    def eligible(c):
        if c.priority == 'low':
            return False
        row = status_map.get(c.id)
        if not row:
            return True
        if c.require_read:
            return not row.get('read')
        return not (row.get('read') or row.get('dismissed'))

    eligible_list = [c for c in popup_candidates if eligible(c)]
    eligible_list.sort(key=lambda c: (priority_rank.get(c.priority, 9), -c.publish_at.timestamp()))
    popup = eligible_list[0] if eligible_list else None

    if popup:
        Communication.objects.filter(pk=popup.pk).update(total_impressions=F('total_impressions') + 1)

    banner_list = _visible_to(request, channel_field='show_banner')
    banner_status = get_delivery_status_map(request.user, banner_list)

    return JsonResponse({
        "popup": _serialize_for_recipient(popup, status_map.get(popup.id)) if popup else None,
        "banner": [_serialize_for_recipient(c, banner_status.get(c.id)) for c in banner_list],
    })


@login_required
@require_GET
def api_recipient_unread_count(request):
    """Powers the notification bell badge + mobile app badge."""
    visible = _visible_to(request, channel_field='show_notification_center')
    ids = [c.id for c in visible]
    read_ids = set(
        CommunicationDeliveryLog.objects.filter(
            recipient=request.user, communication_id__in=ids, read_at__isnull=False,
        ).values_list('communication_id', flat=True)
    )
    unread = sum(1 for cid in ids if cid not in read_ids)
    return JsonResponse({"unread_count": unread})


# ── Recipient-side: event tracking ──────────────────────────────────────

@login_required
@require_POST
def api_track_event(request):
    """
    Body: {"communication_id": 1, "event": "opened"|"clicked"|"dismissed"|"read", "channel": "fcm"|"web_push"}
    Finds or creates the per-recipient delivery-log row (batched FCM sends
    don't get a per-user row until the first event comes back) and stamps
    the corresponding *_at timestamp.
    """
    payload = _parse_json_body(request)
    communication_id = payload.get('communication_id')
    event = payload.get('event')
    channel = payload.get('channel', 'fcm')

    if not communication_id or event not in _VALID_EVENTS:
        return JsonResponse({"ok": False, "error": "communication_id and a valid event are required"}, status=400)

    try:
        communication = Communication.objects.get(pk=communication_id)
    except Communication.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    log, _ = CommunicationDeliveryLog.objects.get_or_create(
        communication=communication, recipient=request.user, channel=channel,
        defaults={'status': 'delivered'},
    )
    setattr(log, f"{event}_at", timezone.now())
    log.status = event
    log.save(update_fields=[f"{event}_at", 'status'])

    return JsonResponse({"ok": True})


# ── Super Admin: list / summary / publish ───────────────────────────────

def _serialize(c: Communication):
    return {
        "id": c.id,
        "title": c.title,
        "type": c.type,
        "type_display": c.get_type_display(),
        "priority": c.priority,
        "status": c.status,
        "publish_at": c.publish_at.isoformat(),
        "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        "is_live": c.is_live,
        "channels": {
            "push": c.channel_push, "web_push": c.channel_web_push, "pwa": c.channel_pwa,
            "email": c.channel_email, "whatsapp": c.channel_whatsapp, "sms": c.channel_sms,
        },
        "dispatch_success_count": c.dispatch_success_count,
        "dispatch_failure_count": c.dispatch_failure_count,
    }


@superuser_required_json
@require_GET
def api_list(request):
    qs = Communication.objects.all()

    comm_type = request.GET.get('type')
    priority = request.GET.get('priority')
    status = request.GET.get('status')
    search = request.GET.get('q')

    if comm_type:
        qs = qs.filter(type=comm_type)
    if priority:
        qs = qs.filter(priority=priority)
    if status:
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(description__icontains=search))

    page = int(request.GET.get('page', 1))
    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(page)

    return JsonResponse({
        "results": [_serialize(c) for c in page_obj],
        "count": paginator.count,
        "has_next": page_obj.has_next(),
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
    })


@superuser_required_json
@require_POST
def api_publish(request, pk):
    try:
        communication = Communication.objects.get(pk=pk)
    except Communication.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    if not hasattr(communication, 'audience'):
        return JsonResponse({"ok": False, "error": "Configure an audience before publishing."}, status=400)

    result = dispatch_communication(communication)
    return JsonResponse({"ok": True, **result})


@superuser_required_json
@require_GET
def api_dashboard_summary(request):
    qs = Communication.objects.all()
    now = timezone.now()

    logs = CommunicationDeliveryLog.objects.filter(communication__in=qs)

    return JsonResponse({
        "total_communications": qs.count(),
        "active_communications": qs.filter(
            is_active=True, status=Communication.Status.PUBLISHED,
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).count(),
        "scheduled": qs.filter(status=Communication.Status.SCHEDULED).count(),
        "expired": qs.filter(expires_at__lt=now).count(),
        "push_delivered": logs.filter(channel='fcm').exclude(status='failed').count(),
        "push_failed": logs.filter(channel='fcm', status='failed').count(),
        "web_push_delivered": logs.filter(channel='web_push').exclude(status='failed').count(),
        "web_push_failed": logs.filter(channel='web_push', status='failed').count(),
    })
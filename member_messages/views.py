# member_messages/views.py
"""
Thin views — all business logic lives in services.py.

Owner/Receptionist side reuses the project's existing gym-scoping +
role-check decorators (_gym_staff_required / _gym_role_required) so this
feature is gated identically to every other staff feature in the app.

Member side reuses active_member_required so only a member with a live,
non-deleted enrollment at the current gym can see their own inbox/popup —
same guard used by Profile / attendance_page / etc.
"""
import json
import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from AuthFit.views import _gym_role_required
from AuthFit.decorators import active_member_required
from AuthFit.models import Enrollment

from . import services

logger = logging.getLogger(__name__)

OWNER_OR_RECEPTIONIST = ('gym_owner', 'receptionist')


def _truthy(val):
    return val in ('on', '1', 'true', 'True', True)


# ══════════════════════════════════════════════════════════════════════════
# OWNER / RECEPTIONIST — Communication > Member Messages
# ══════════════════════════════════════════════════════════════════════════

@_gym_role_required(*OWNER_OR_RECEPTIONIST)
def member_message_list(request):
    """GET /member-messages/ — sidebar 'Member Messages' page."""
    gym = getattr(request, 'gym', None)
    search = request.GET.get('q', '').strip()
    page_obj = services.get_owner_message_list(gym, search=search, page=request.GET.get('page', 1))
    services.attach_member_enrollment(page_obj, gym)

    can_delete = getattr(request, 'staff_role', None) == 'gym_owner' or getattr(request, 'is_super_admin', False)

    return render(request, 'member_messages/list.html', {
        'gym': gym,
        'page_obj': page_obj,
        'search': search,
        'can_delete': can_delete,
        'stats': services.get_dashboard_stats(gym),
    })


@_gym_role_required(*OWNER_OR_RECEPTIONIST)
def member_message_history(request, member_id):
    """
    GET /member-messages/history/<member_id>/
    Backs the 'Message History' section on the Member Detail page.
    Returns JSON for the AJAX-loaded panel, full HTML otherwise.
    """
    gym = getattr(request, 'gym', None)
    enrollment = get_object_or_404(
        Enrollment.objects.select_related('user'), pk=member_id, gym=gym,
    )

    if not enrollment.user_id:
        # Pending signup — no linked account, so no messages could exist yet.
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'rows': [], 'has_next': False, 'has_previous': False,
                                  'current_page': 1, 'num_pages': 1})
        return render(request, 'member_messages/history.html', {
            'gym': gym, 'member': enrollment, 'page_obj': None, 'search': '',
        })

    search = request.GET.get('q', '').strip()
    page_obj = services.get_member_history(
        gym, enrollment.user, search=search, page=request.GET.get('page', 1),
    )

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        rows = [
            {
                'id': m.id,
                'title': m.title,
                'priority': m.get_priority_display(),
                'priority_key': m.priority,
                'status': m.status_display,
                'created_at': m.created_at.strftime('%d %b %Y, %I:%M %p'),
            }
            for m in page_obj
        ]
        return JsonResponse({
            'rows': rows,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
            'current_page': page_obj.number,
            'num_pages': page_obj.paginator.num_pages,
        })

    return render(request, 'member_messages/history.html', {
        'gym': gym,
        'member': enrollment,
        'page_obj': page_obj,
        'search': search,
    })


@_gym_role_required(*OWNER_OR_RECEPTIONIST)
@require_POST
def member_message_send(request):
    """
    POST /member-messages/send/
    Body: member_id, title, message, priority, show_popup, send_push, save_inbox, next
    PRG pattern — redirects back to `next` (defaults to the referring page).
    """
    gym = getattr(request, 'gym', None)
    member_id = request.POST.get('member_id', '').strip()
    redirect_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/member-messages/'

    enrollment = Enrollment.objects.filter(gym=gym, pk=member_id).select_related('user').first()
    if not enrollment or not enrollment.user_id:
        messages.error(request, "Selected member is invalid or not linked to an account yet.")
        return redirect(redirect_url)

    try:
        services.send_member_message(
            gym=gym,
            member=enrollment.user,
            created_by=request.user,
            title=request.POST.get('title', ''),
            message=request.POST.get('message', ''),
            priority=request.POST.get('priority', 'normal'),
            show_popup=_truthy(request.POST.get('show_popup')),
            send_push=_truthy(request.POST.get('send_push')),
            save_inbox=_truthy(request.POST.get('save_inbox')),
        )
        messages.success(request, f"Message sent to {enrollment.fullname}.")
    except services.MemberMessageError as exc:
        messages.error(request, str(exc))

    return redirect(redirect_url)


@_gym_role_required('gym_owner')
@require_POST
def member_message_delete(request, message_id):
    """
    DELETE (as POST) /member-messages/<id>/delete/
    Gym Owner only — Receptionists are explicitly denied delete per the spec's
    permission matrix (permissions.receptionist.delete = false).
    """
    gym = getattr(request, 'gym', None)
    try:
        services.delete_message(gym, message_id)
        return JsonResponse({'success': True})
    except services.MemberMessageError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=404)


@active_member_required
@require_GET
def api_member_messages(request):
    """GET /api/member-messages/ — paginated inbox JSON."""
    gym = getattr(request, 'gym', None)
    page_obj = services.get_member_inbox(gym, request.user, page=request.GET.get('page', 1))
    return JsonResponse({
        'results': [
            {
                'id': m.id,
                'title': m.title,
                'message': m.message,
                'priority': m.priority,
                'is_read': m.is_read,
                'created_at': m.created_at.strftime('%d %b %Y, %I:%M %p'),
            }
            for m in page_obj
        ],
        'has_next': page_obj.has_next(),
        'current_page': page_obj.number,
        'num_pages': page_obj.paginator.num_pages,
    })


@active_member_required
@require_GET
def api_member_messages_home(request):
    """GET /api/member-messages/home/ — popup endpoint (unread + show_popup)."""
    gym = getattr(request, 'gym', None)
    popups = services.get_popup_messages(gym, request.user)
    return JsonResponse({
        'popups': [
            {
                'id': m.id,
                'title': m.title,
                'message': m.message,
                'priority': m.priority,
                'created_at': m.created_at.strftime('%d %b %Y, %I:%M %p'),
            }
            for m in popups
        ]
    })


@active_member_required
@require_POST
def api_member_messages_read(request):
    """POST /api/member-messages/read/ — body: {"message_id": <id>}."""
    gym = getattr(request, 'gym', None)
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    message_id = body.get('message_id')
    if not message_id:
        return JsonResponse({'error': 'message_id is required'}, status=400)

    try:
        services.mark_message_read(gym, request.user, message_id)
        return JsonResponse({'success': True})
    except services.MemberMessageError as exc:
        return JsonResponse({'error': str(exc)}, status=404)


@active_member_required
@require_GET
def api_member_messages_unread_count(request):
    """GET /api/member-messages/unread-count/"""
    gym = getattr(request, 'gym', None)
    return JsonResponse({'unread_count': services.get_unread_count(gym, request.user)})
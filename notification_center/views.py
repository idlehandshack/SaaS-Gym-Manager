"""
notification_center/views.py

Single recipient-facing view, available to every authenticated role
(member, gym_owner, receptionist, trainer). Pagination happens in Python
over the already-merged, already-sorted list — the three source queries
are each small and indexed (per-gym for announcements/messages, audience-
resolved for communications), so this stays cheap without needing a SQL
UNION across differently-shaped tables.
"""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from . import services


@login_required
def notification_center(request):
    gym = getattr(request, 'gym', None)
    role = services.get_role_for_user(request.user)
    items = services.get_unified_notifications(request.user, gym=gym, role=role)
    type_filter = request.GET.get('type') or None
    read_filter = request.GET.get('status') or None  # 'read' | 'unread'
    search = request.GET.get('q') or None

    filtered = services.apply_filters(items, type_filter=type_filter, read_filter=read_filter, search=search)

    unread_count = sum(1 for i in items if not i.is_read)

    paginator = Paginator(filtered, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'notification_center/index.html', {
        'page_obj': page_obj,
        'role': role,
        'isStaff': role != 'member',
        'unread_count': unread_count,
        'total_count': len(items),
        'filters': {
            'type': type_filter or '',
            'status': read_filter or '',
            'q': request.GET.get('q') or '',
        },
    })


@login_required
@require_POST
def notification_center_mark_read(request, key):
    gym = getattr(request, 'gym', None)
    ok = services.mark_item_read(request.user, gym, key)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': ok})

    from django.shortcuts import redirect
    return redirect('notification_center')

@login_required
def notification_center_unread_count(request):
    gym = getattr(request, 'gym', None)
    role = services.get_role_for_user(request.user)
    items = services.get_unified_notifications(request.user, gym=gym, role=role)
    unread_count = sum(1 for i in items if not i.is_read)
    return JsonResponse({'unread_count': unread_count})
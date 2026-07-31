"""
announcements/views.py

Owner-facing CRUD/analytics screens (Django templates) + member-facing
Announcement Center page. JSON APIs live in api.py.

Follows the existing project convention: function-based views, PRG
(post/redirect/get) on writes, `messages` framework for flash feedback,
tenant isolation enforced via `permissions.get_announcement_or_404` /
`resolve_gym_for_staff`.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from Gym.models import Gym

from .forms import AnnouncementForm
from .models import Announcement, AnnouncementRead
from .permissions import (
    announcement_admin_required,
    announcement_write_required,
    can_manage_announcements,
    get_announcement_or_404,
    resolve_gym_for_staff,
)
from .utils import send_announcement_push

logger = logging.getLogger(__name__)


def _current_gym(request):
    """SuperAdmin may switch gyms via ?gym=<uuid>; staff are pinned to their own."""
    if request.user.is_superuser:
        gym_id = request.GET.get('gym')
        if gym_id:
            return get_object_or_404(Gym, pk=gym_id)
        return None
    return resolve_gym_for_staff(request.user)


# ── Owner: List ──────────────────────────────────────────────────────────

@announcement_admin_required
def announcement_list(request):
    gym = _current_gym(request)
    qs = Announcement.objects.select_related('gym').order_by('-pin_home', '-publish_at')
    if gym:
        qs = qs.filter(gym=gym)
    elif not request.user.is_superuser:
        qs = qs.none()

    category = request.GET.get('category')
    priority = request.GET.get('priority')
    status   = request.GET.get('status')  # active | expired | inactive
    search   = request.GET.get('q')

    if category:
        qs = qs.filter(announcement_type=category)
    if priority:
        qs = qs.filter(priority=priority)
    if status == 'active':
        qs = qs.filter(is_active=True, expires_at__isnull=True) | qs.filter(
            is_active=True, expires_at__gt=timezone.now())
    elif status == 'expired':
        qs = qs.filter(expires_at__lt=timezone.now())
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(description__icontains=search))

    paginator = Paginator(qs.distinct(), 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'announcements/owner/list.html', {
        'page_obj': page_obj,
        'categories': Announcement.Category.choices,
        'priorities': Announcement.Priority.choices,
        'filters': {'category': category, 'priority': priority, 'status': status, 'q': search or ''},
        'gyms': Gym.objects.all() if request.user.is_superuser else None,
        'selected_gym': gym,
    })


# ── Owner: Create ───────────────────────────────────────────────────────

@announcement_write_required
def announcement_create(request):
    gym = _current_gym(request)
    if not gym:
        messages.error(request, "Select a gym before creating an announcement.")
        return redirect('announcement_list')

    if request.method == 'POST':
        form = AnnouncementForm(request.POST, request.FILES, gym=gym)
        if form.is_valid():
            announcement = form.save(commit=False)
            announcement.gym = gym
            announcement.created_by = request.user
            announcement.save()
            form.save_m2m()

            if announcement.send_push and announcement.is_live:
                send_announcement_push(announcement)

            messages.success(request, f"Announcement '{announcement.title}' created.")
            return redirect('announcement_list')
    else:
        form = AnnouncementForm(gym=gym)

    return render(request, 'announcements/owner/form.html', {'form': form, 'is_edit': False})


# ── Owner: Edit ──────────────────────────────────────────────────────────

@announcement_write_required
def announcement_edit(request, pk):
    announcement = get_announcement_or_404(request, pk)

    if request.method == 'POST':
        form = AnnouncementForm(request.POST, request.FILES, instance=announcement, gym=announcement.gym)
        if form.is_valid():
            was_pushed = announcement.push_sent_at is not None
            announcement = form.save()

            if announcement.send_push and not was_pushed and announcement.is_live:
                send_announcement_push(announcement)

            messages.success(request, f"Announcement '{announcement.title}' updated.")
            return redirect('announcement_list')
    else:
        form = AnnouncementForm(instance=announcement, gym=announcement.gym)

    return render(request, 'announcements/owner/form.html', {
        'form': form, 'is_edit': True, 'announcement': announcement,
    })


@announcement_write_required
@require_POST
def announcement_delete(request, pk):
    announcement = get_announcement_or_404(request, pk)
    title = announcement.title
    announcement.delete()
    messages.success(request, f"Announcement '{title}' deleted.")
    return redirect('announcement_list')


@announcement_write_required
@require_POST
def announcement_toggle_active(request, pk):
    announcement = get_announcement_or_404(request, pk)
    announcement.is_active = not announcement.is_active
    announcement.save(update_fields=['is_active'])
    return redirect('announcement_list')


@announcement_write_required
@require_POST
def announcement_send_push_now(request, pk):
    """Manual 'resend/send now' trigger — owner explicitly re-fires push."""
    announcement = get_announcement_or_404(request, pk)
    if not announcement.is_live:
        messages.error(request, "Only a live (published, not expired) announcement can be pushed.")
        return redirect('announcement_list')
    sent = send_announcement_push(announcement)
    messages.success(request, f"Push sent to {sent} device(s).")
    return redirect('announcement_list')


# ── Owner: Archive (soft view — expired/inactive only) ──────────────────

@announcement_admin_required
def announcement_archive(request):
    gym = _current_gym(request)
    qs = Announcement.objects.filter(
        Q(is_active=False) | Q(expires_at__lt=timezone.now())
    ).order_by('-updated_at')
    if gym:
        qs = qs.filter(gym=gym)
    elif not request.user.is_superuser:
        qs = qs.none()

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'announcements/owner/archive.html', {'page_obj': page_obj})


# ── Owner: Analytics ─────────────────────────────────────────────────────

@announcement_admin_required
def announcement_analytics(request):
    gym = _current_gym(request)
    qs = Announcement.objects.all()
    if gym:
        qs = qs.filter(gym=gym)
    elif not request.user.is_superuser:
        qs = qs.none()

    now = timezone.now()
    total_announcements   = qs.count()
    active_announcements  = qs.filter(is_active=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).count()
    expired_announcements = qs.filter(expires_at__lt=now).count()

    reads = AnnouncementRead.objects.filter(announcement__in=qs)
    total_reads      = reads.filter(read_at__isnull=False).count()
    dismissed_count  = reads.filter(dismissed=True).count()
    total_views      = qs.aggregate(v=Count('id'))['v'] and sum(qs.values_list('view_count', flat=True))
    push_sent_count  = sum(qs.values_list('push_sent_count', flat=True))

    read_percentage = round((total_reads / total_views) * 100, 1) if total_views else 0

    top_announcements = (
        qs.annotate(read_total=Count('reads', filter=Q(reads__read_at__isnull=False)))
        .order_by('-read_total')[:10]
    )

    return render(request, 'announcements/owner/analytics.html', {
        'total_announcements': total_announcements,
        'active_announcements': active_announcements,
        'expired_announcements': expired_announcements,
        'total_views': total_views,
        'total_reads': total_reads,
        'dismissed_count': dismissed_count,
        'push_sent_count': push_sent_count,
        'read_percentage': read_percentage,
        'top_announcements': top_announcements,
    })
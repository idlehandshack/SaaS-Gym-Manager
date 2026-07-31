"""
communications/views.py

Super Admin CRUD + analytics + dashboard screens, plus one recipient-facing
screen (communication_center, any authenticated member/owner/receptionist/
trainer). Follows the same conventions announcements/views.py uses:
function-based views, PRG on writes, `messages` framework for flash
feedback. Templates extend communications/templates/superadmin/base.html.
"""

import csv
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    CommunicationAudienceForm, CommunicationCampaignForm, CommunicationForm,
    CommunicationSponsorForm,
)
from .models import (
    Communication, CommunicationAudience, CommunicationCampaign,
    CommunicationDeliveryLog, CommunicationSponsor, log_communication_action,
)
from .permissions import superuser_required
from .services import dispatch_communication, get_delivery_status_map, get_visible_communications

logger = logging.getLogger(__name__)

DASHBOARD_CACHE_KEY = 'communications_dashboard_stats'
DASHBOARD_CACHE_TTL_SECONDS = 30  # short — this is an admin dashboard, not a public page


@superuser_required
def communication_list(request):
    qs = Communication.objects.select_related('campaign', 'campaign__sponsor', 'created_by')

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
        # search.requirements: title, sponsor, campaign, creator, status, channel
        qs = qs.filter(
            Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(campaign__name__icontains=search)
            | Q(campaign__sponsor__name__icontains=search)
            | Q(created_by__username__icontains=search)
            | Q(status__icontains=search)
        )

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'communications/admin/list.html', {
        'page_obj': page_obj,
        'types': Communication.Type.choices,
        'priorities': Communication.Priority.choices,
        'statuses': Communication.Status.choices,
        'filters': {'type': comm_type, 'priority': priority, 'status': status, 'q': search or ''},
        'active_nav': 'communications',
    })


@superuser_required
@require_POST
def communication_bulk_action(request):
    """
    Bulk publish / cancel / delete (soft) / duplicate, per
    bulk_operations.tasks. One shared endpoint rather than four near-
    identical views — the action name picks the branch, each branch reuses
    the exact same single-item logic (dispatch_communication, soft_delete,
    etc.) that communication_publish_now/_cancel/_delete already use.
    """
    action = request.POST.get('bulk_action')
    ids = request.POST.getlist('selected')

    if not ids:
        messages.warning(request, "No communications selected.")
        return redirect('communication_list')

    communications = list(Communication.objects.filter(pk__in=ids))
    if not communications:
        messages.error(request, "None of the selected communications could be found.")
        return redirect('communication_list')

    if action == 'publish':
        published, skipped = 0, 0
        for c in communications:
            if not hasattr(c, 'audience'):
                skipped += 1
                continue
            result = dispatch_communication(c)
            if result.get('skipped'):
                skipped += 1
                continue
            c.published_by = request.user
            c.save(update_fields=['published_by'])
            log_communication_action(c, 'published', actor=request.user, detail='bulk publish')
            published += 1
        messages.success(request, f"Published {published} communication(s)." + (f" {skipped} skipped." if skipped else ""))

    elif action == 'cancel':
        for c in communications:
            c.status = Communication.Status.CANCELLED
            c.is_active = False
            c.cancelled_by = request.user
            c.save(update_fields=['status', 'is_active', 'cancelled_by'])
            log_communication_action(c, 'cancelled', actor=request.user, detail='bulk cancel')
        messages.success(request, f"Cancelled {len(communications)} communication(s).")

    elif action == 'delete':
        for c in communications:
            c.soft_delete(user=request.user)
            log_communication_action(c, 'deleted', actor=request.user, detail='bulk delete')
        messages.success(request, f"Deleted {len(communications)} communication(s).")

    elif action == 'duplicate':
        duplicated = 0
        for c in communications:
            audience = getattr(c, 'audience', None)
            c.pk = None
            c.title = f"{c.title} (Copy)"
            c.status = Communication.Status.DRAFT
            c.dispatched_at = None
            c.dispatch_success_count = 0
            c.dispatch_failure_count = 0
            c.is_dispatching = False
            c.total_impressions = 0
            c.created_by = request.user
            c.updated_by = None
            c.published_by = None
            c.cancelled_by = None
            c.is_deleted = False
            c.deleted_at = None
            c.deleted_by = None
            c.save()
            if audience:
                gyms, plans, subscription_plans = list(audience.gyms.all()), list(audience.plans.all()), list(audience.subscription_plans.all())
                specific_members, specific_staff = list(audience.specific_members.all()), list(audience.specific_staff.all())
                audience.pk = None
                audience.communication = c
                audience.save()
                audience.gyms.set(gyms)
                audience.plans.set(plans)
                audience.subscription_plans.set(subscription_plans)
                audience.specific_members.set(specific_members)
                audience.specific_staff.set(specific_staff)
            log_communication_action(c, 'created', actor=request.user, detail='duplicated')
            duplicated += 1
        messages.success(request, f"Duplicated {duplicated} communication(s) as new drafts.")

    else:
        messages.error(request, "Unknown bulk action.")

    return redirect('communication_list')


@superuser_required
def communication_create(request):
    if request.method == 'POST':
        form = CommunicationForm(request.POST, request.FILES)
        audience_form = CommunicationAudienceForm(request.POST)
        if form.is_valid() and audience_form.is_valid():
            communication = form.save(commit=False)
            communication.created_by = request.user
            communication.status = Communication.Status.DRAFT
            communication.save()
            form.save_m2m()

            audience = audience_form.save(commit=False)
            audience.communication = communication
            audience.save()
            audience_form.save_m2m()

            log_communication_action(communication, 'created', actor=request.user)
            messages.success(request, f"Communication '{communication.title}' created as a draft.")
            return redirect('communication_list')
    else:
        form = CommunicationForm()
        audience_form = CommunicationAudienceForm()

    return render(request, 'communications/admin/form.html', {
        'form': form, 'audience_form': audience_form, 'is_edit': False,
        'active_nav': 'communications',
    })


@superuser_required
def communication_edit(request, pk):
    communication = get_object_or_404(Communication, pk=pk)
    audience, _ = CommunicationAudience.objects.get_or_create(communication=communication)

    if request.method == 'POST':
        form = CommunicationForm(request.POST, request.FILES, instance=communication)
        audience_form = CommunicationAudienceForm(request.POST, instance=audience)
        if form.is_valid() and audience_form.is_valid():
            communication = form.save(commit=False)
            communication.updated_by = request.user
            communication.save()
            form.save_m2m()
            audience_form.save()
            log_communication_action(communication, 'updated', actor=request.user)
            messages.success(request, f"Communication '{communication.title}' updated.")
            return redirect('communication_list')
    else:
        form = CommunicationForm(instance=communication)
        audience_form = CommunicationAudienceForm(instance=audience)

    return render(request, 'communications/admin/form.html', {
        'form': form, 'audience_form': audience_form, 'is_edit': True, 'communication': communication,
        'active_nav': 'communications',
    })


@superuser_required
@require_POST
def communication_delete(request, pk):
    communication = get_object_or_404(Communication, pk=pk)
    title = communication.title
    communication.soft_delete(user=request.user)
    log_communication_action(communication, 'deleted', actor=request.user)
    messages.success(request, f"Communication '{title}' deleted.")
    return redirect('communication_list')


@superuser_required
@require_POST
def communication_publish_now(request, pk):
    """Manual 'Publish Now' trigger — resolves the audience and fans out
    across every enabled channel via CommunicationDispatcher."""
    communication = get_object_or_404(Communication, pk=pk)

    if not hasattr(communication, 'audience'):
        messages.error(request, "Configure an audience before publishing.")
        return redirect('communication_edit', pk=pk)

    result = dispatch_communication(communication)

    if result.get('skipped'):
        messages.warning(
            request,
            f"'{communication.title}' was already published or is currently dispatching elsewhere — "
            f"skipped to avoid a duplicate send.",
        )
        return redirect('communication_list')

    communication.published_by = request.user
    communication.save(update_fields=['published_by'])
    log_communication_action(
        communication, 'published', actor=request.user,
        detail=f"success={result['success']} failure={result['failure']}",
    )
    messages.success(
        request,
        f"Dispatched '{communication.title}' — {result['success']} delivered, {result['failure']} failed.",
    )
    return redirect('communication_list')


@superuser_required
@require_POST
def communication_cancel(request, pk):
    communication = get_object_or_404(Communication, pk=pk)
    communication.status = Communication.Status.CANCELLED
    communication.is_active = False
    communication.cancelled_by = request.user
    communication.save(update_fields=['status', 'is_active', 'cancelled_by'])
    log_communication_action(communication, 'cancelled', actor=request.user)
    messages.success(request, f"'{communication.title}' cancelled — it will not be dispatched.")
    return redirect('communication_list')


@superuser_required
def communication_analytics(request):
    qs = Communication.objects.all()
    now = timezone.now()

    total = qs.count()
    active = qs.filter(is_active=True, status=Communication.Status.PUBLISHED).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    ).count()
    scheduled = qs.filter(status=Communication.Status.SCHEDULED).count()
    expired = qs.filter(expires_at__lt=now).count()

    logs = CommunicationDeliveryLog.objects.filter(communication__in=qs)
    push_delivered = logs.filter(channel='fcm', status__in=['sent', 'delivered']).count()
    push_failed = logs.filter(channel='fcm', status='failed').count()
    total_impressions = qs.aggregate(v=Count('id'))['v'] and sum(qs.values_list('total_impressions', flat=True))
    opened = logs.filter(opened_at__isnull=False).count()
    clicked = logs.filter(clicked_at__isnull=False).count()
    dismissed = logs.filter(dismissed_at__isnull=False).count()
    read_count = logs.filter(read_at__isnull=False).count()
    sent_total = logs.exclude(status='failed').count()

    def pct(numerator, denominator):
        return round((numerator / denominator) * 100, 1) if denominator else 0

    daily_counts = _daily_communication_counts(qs, days=14)

    return render(request, 'communications/admin/analytics.html', {
        'active_nav': 'communications_analytics',
        'total_communications': total,
        'active_communications': active,
        'scheduled_communications': scheduled,
        'expired_communications': expired,
        'push_delivered': push_delivered,
        'push_failed': push_failed,
        'total_impressions': total_impressions,
        'open_rate': pct(opened, sent_total),
        'read_rate': pct(read_count, sent_total),
        'click_rate': pct(clicked, sent_total),
        'dismiss_rate': pct(dismissed, sent_total),
        # Simple CSS-bar charts — no chart.js dependency needed for these two.
        'daily_counts': daily_counts,
        'max_daily_count': max([d['count'] for d in daily_counts] or [1]) or 1,
        'channel_counts': {
            'fcm': logs.filter(channel='fcm').exclude(status='failed').count(),
            'web_push': logs.filter(channel='web_push').exclude(status='failed').count(),
        },
    })


def _daily_communication_counts(qs, days=14):
    """[(date, count), ...] of communications published per day over the
    last `days` days — backs the 'Daily Communications' chart."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    counts = {start + timedelta(days=i): 0 for i in range(days)}
    for publish_at in qs.filter(publish_at__date__gte=start).values_list('publish_at', flat=True):
        d = timezone.localtime(publish_at).date()
        if d in counts:
            counts[d] += 1
    return [{'date': d, 'count': c} for d, c in sorted(counts.items())]


# ── Super Admin: Dashboard ───────────────────────────────────────────────

@superuser_required
def communication_dashboard(request):
    """Landing page for the Communications section — recent items, status
    counts, delivery stats, and campaign/sponsor summaries, per the
    super_admin_dashboard integration task. Aggregate stats are cached
    briefly since this page is meant to be glanced at often; recent_
    communications itself is always fetched fresh (cheap, and the whole
    point of a 'recent' list is being current)."""
    qs = Communication.objects.all()
    now = timezone.now()

    recent = qs.select_related('campaign')[:10]

    stats = cache.get(DASHBOARD_CACHE_KEY)
    if stats is None:
        logs = CommunicationDeliveryLog.objects.filter(communication__in=qs)
        campaign_qs = CommunicationCampaign.objects.all()
        sponsor_qs = CommunicationSponsor.objects.all()
        stats = {
            'draft_count': qs.filter(status=Communication.Status.DRAFT).count(),
            'scheduled_count': qs.filter(status=Communication.Status.SCHEDULED).count(),
            'published_count': qs.filter(status=Communication.Status.PUBLISHED).count(),
            'expired_count': qs.filter(expires_at__lt=now).count(),
            'push_delivered': logs.filter(channel='fcm').exclude(status='failed').count(),
            'push_failed': logs.filter(channel='fcm', status='failed').count(),
            'campaign_total': campaign_qs.count(),
            'campaign_active': campaign_qs.filter(status=CommunicationCampaign.Status.ACTIVE).count(),
            'sponsor_total': sponsor_qs.count(),
            'sponsor_active': sponsor_qs.filter(status=CommunicationSponsor.Status.ACTIVE).count(),
        }
        cache.set(DASHBOARD_CACHE_KEY, stats, DASHBOARD_CACHE_TTL_SECONDS)

    return render(request, 'communications/admin/dashboard.html', {
        'active_nav': 'communications_dashboard',
        'recent_communications': recent,
        **stats,
    })


# ── Super Admin: Delivery Logs (read-only) ───────────────────────────────

@superuser_required
def delivery_log_list(request):
    qs = CommunicationDeliveryLog.objects.select_related('communication', 'gym', 'recipient')

    channel = request.GET.get('channel')
    status = request.GET.get('status')
    comm_id = request.GET.get('communication')

    if channel:
        qs = qs.filter(channel=channel)
    if status:
        qs = qs.filter(status=status)
    if comm_id:
        qs = qs.filter(communication_id=comm_id)

    paginator = Paginator(qs, 40)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'communications/admin/delivery_logs.html', {
        'active_nav': 'communications_delivery_logs',
        'page_obj': page_obj,
        'channels': CommunicationDeliveryLog.CHANNEL_CHOICES,
        'statuses': CommunicationDeliveryLog.STATUS_CHOICES,
        'filters': {'channel': channel or '', 'status': status or '', 'communication': comm_id or ''},
    })


@superuser_required
def delivery_log_export(request):
    """CSV export of delivery logs — same filters as delivery_log_list,
    per delivery_logs.tasks: 'CSV export'. Streams via csv.writer directly
    into the HttpResponse rather than building a string in memory first."""
    qs = CommunicationDeliveryLog.objects.select_related('communication', 'gym', 'recipient')

    channel = request.GET.get('channel')
    status = request.GET.get('status')
    comm_id = request.GET.get('communication')
    if channel:
        qs = qs.filter(channel=channel)
    if status:
        qs = qs.filter(status=status)
    if comm_id:
        qs = qs.filter(communication_id=comm_id)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="communication_delivery_logs.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Communication', 'Gym', 'Recipient', 'Channel', 'Status',
        'Delivered At', 'Opened At', 'Clicked At', 'Dismissed At', 'Read At', 'Error', 'Created At',
    ])
    for log in qs.iterator(chunk_size=500):
        writer.writerow([
            log.communication.title if log.communication_id else '(deleted)',
            log.gym.gym_name if log.gym_id else '',
            log.recipient.username if log.recipient_id else '(batch)',
            log.get_channel_display(),
            log.get_status_display(),
            log.delivered_at or '', log.opened_at or '', log.clicked_at or '',
            log.dismissed_at or '', log.read_at or '',
            log.error, log.created_at,
        ])
    return response


# ── Super Admin: Sponsors ────────────────────────────────────────────────

@superuser_required
def sponsor_list(request):
    qs = CommunicationSponsor.objects.annotate(campaign_count=Count('campaigns'))
    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'communications/admin/sponsors_list.html', {
        'active_nav': 'communications_sponsors', 'page_obj': page_obj,
    })


@superuser_required
def sponsor_create(request):
    if request.method == 'POST':
        form = CommunicationSponsorForm(request.POST, request.FILES)
        if form.is_valid():
            sponsor = form.save()
            messages.success(request, f"Sponsor '{sponsor.name}' created.")
            return redirect('sponsor_list')
    else:
        form = CommunicationSponsorForm()
    return render(request, 'communications/admin/sponsor_form.html', {
        'active_nav': 'communications_sponsors', 'form': form, 'is_edit': False,
    })


@superuser_required
def sponsor_edit(request, pk):
    sponsor = get_object_or_404(CommunicationSponsor, pk=pk)
    if request.method == 'POST':
        form = CommunicationSponsorForm(request.POST, request.FILES, instance=sponsor)
        if form.is_valid():
            form.save()
            messages.success(request, f"Sponsor '{sponsor.name}' updated.")
            return redirect('sponsor_list')
    else:
        form = CommunicationSponsorForm(instance=sponsor)
    return render(request, 'communications/admin/sponsor_form.html', {
        'active_nav': 'communications_sponsors', 'form': form, 'is_edit': True, 'sponsor': sponsor,
    })


@superuser_required
@require_POST
def sponsor_delete(request, pk):
    sponsor = get_object_or_404(CommunicationSponsor, pk=pk)
    name = sponsor.name
    sponsor.delete()
    messages.success(request, f"Sponsor '{name}' deleted.")
    return redirect('sponsor_list')


# ── Super Admin: Campaigns ───────────────────────────────────────────────

@superuser_required
def campaign_list(request):
    qs = CommunicationCampaign.objects.select_related('sponsor').annotate(
        communication_count=Count('communications'),
    )
    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'communications/admin/campaigns_list.html', {
        'active_nav': 'communications_campaigns', 'page_obj': page_obj,
    })


@superuser_required
def campaign_create(request):
    if request.method == 'POST':
        form = CommunicationCampaignForm(request.POST)
        if form.is_valid():
            campaign = form.save()
            messages.success(request, f"Campaign '{campaign.name}' created.")
            return redirect('campaign_list')
    else:
        form = CommunicationCampaignForm()
    return render(request, 'communications/admin/campaign_form.html', {
        'active_nav': 'communications_campaigns', 'form': form, 'is_edit': False,
    })


@superuser_required
def campaign_edit(request, pk):
    campaign = get_object_or_404(CommunicationCampaign, pk=pk)
    if request.method == 'POST':
        form = CommunicationCampaignForm(request.POST, instance=campaign)
        if form.is_valid():
            form.save()
            messages.success(request, f"Campaign '{campaign.name}' updated.")
            return redirect('campaign_list')
    else:
        form = CommunicationCampaignForm(instance=campaign)
    return render(request, 'communications/admin/campaign_form.html', {
        'active_nav': 'communications_campaigns', 'form': form, 'is_edit': True, 'campaign': campaign,
    })


@superuser_required
@require_POST
def campaign_delete(request, pk):
    campaign = get_object_or_404(CommunicationCampaign, pk=pk)
    name = campaign.name
    campaign.delete()
    messages.success(request, f"Campaign '{name}' deleted.")
    return redirect('campaign_list')
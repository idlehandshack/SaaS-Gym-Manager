"""
billing/views_owner.py
-----------------------
Views accessible ONLY by gym_owner role.
Every function is decorated with @_gym_owner_required which enforces:

    request.staff_role == 'gym_owner'   (or is_super_admin)

Receptionists, trainers, and members are all blocked even if they guess the URL.
All DB queries are scoped to request.gym — no cross-gym access is possible.
"""
import functools
import json
import logging
from datetime import date
from django.db.models import Count, Sum
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET
from billing.models import Invoice, Payment
from billing.services.monthly_report import (
    generate_monthly_report_excel,
    get_all_months_summary,
    get_monthly_report_data,
)
from billing.services.pdf_generator import generate_invoice_pdf

logger = logging.getLogger('billing')


# ──────────────────────────────────────────────────────────────────────────────
# Access guard — gym_owner only
# ──────────────────────────────────────────────────────────────────────────────

def _gym_owner_required(view_fn):
    """
    Decorator that enforces gym_owner role.
    Stack order: login_required → staff check → owner-role check.
    Super admins always pass (they manage all gyms).
    """
    @login_required
    @functools.wraps(view_fn)
    def wrapped(request, *args, **kwargs):
        # Must be gym staff first
        if not getattr(request, 'is_gym_staff', False):
            return HttpResponseForbidden("Staff access required.")
        # Super admin bypasses role check
        if getattr(request, 'is_super_admin', False):
            return view_fn(request, *args, **kwargs)
        # Gym must be resolved
        if not getattr(request, 'gym', None):
            return HttpResponseForbidden("No gym context.")
        # Role check — receptionists and trainers are blocked here
        if getattr(request, 'staff_role', None) != 'gym_owner':
            return HttpResponseForbidden(
                "This page is restricted to gym owners only."
            )
        return view_fn(request, *args, **kwargs)
    return wrapped


def _gym(request):
    """Shorthand — always returns the request-scoped gym."""
    return getattr(request, 'gym', None)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Owner Dashboard
# ──────────────────────────────────────────────────────────────────────────────

@_gym_owner_required
def owner_dashboard(request):
    """
    Landing page for gym owners.
    Shows:
      - Owner identity (username + Gym Owner badge)
      - Quick KPI cards (current month)
      - Link to full monthly revenue report
      - Link to invoice list
    """
    gym   = _gym(request)
    today = timezone.localdate()

    # Current-month KPIs (light query — just aggregates, no invoice objects)
    start = date(today.year, today.month, 1)
    end   = date(today.year + 1, 1, 1) if today.month == 12 \
            else date(today.year, today.month + 1, 1)

    agg = (
        Payment.objects
        .filter(gym=gym, payment_date__gte=start, payment_date__lt=end)
        .aggregate(
            total_paid=Sum('paid_amount'),
            total_pending=Sum('pending_amount'),
            total_count=Count('id'),
        )
    )
    # Cleaner import pattern
    agg = (
        Payment.objects
        .filter(gym=gym, payment_date__gte=start, payment_date__lt=end)
        .aggregate(
            total_paid=Sum('paid_amount'),
            total_pending=Sum('pending_amount'),
            total_count=Count('id'),
        )
    )

    total_invoices_this_month = (
        Invoice.objects
        .filter(gym=gym, invoice_date__gte=start, invoice_date__lt=end, status='issued')
        .count()
    )

    # Last 2 invoices for the dashboard preview
    recent_invoices = (
        Invoice.objects
        .filter(gym=gym, status='issued')
        .order_by('-invoice_date', '-invoice_number')[:5]
    )

    # Month list for the month-picker widget
    months_summary = get_all_months_summary(gym, num_months=6)

    return render(request, 'billing/owner_dashboard.html', {
        'gym':                      gym,
        'owner_username':           request.user.username,
        'today':                    today,
        'current_month_label':      today.strftime('%B %Y'),
        'total_paid_this_month':    float(agg['total_paid'] or 0),
        'total_pending_this_month': float(agg['total_pending'] or 0),
        'total_payments_this_month':agg['total_count'] or 0,
        'total_invoices_this_month':total_invoices_this_month,
        'recent_invoices':          recent_invoices,
        'months_summary':           months_summary,
        # Pass current year/month for default report link
        'current_year':             today.year,
        'current_month':            today.month,
    })


# ──────────────────────────────────────────────────────────────────────────────
# 2. Monthly Reports — list from R2
# ──────────────────────────────────────────────────────────────────────────────

@_gym_owner_required
@require_GET
def owner_monthly_report_list(request):
    """
    Lists all monthly revenue reports stored in Cloudflare R2 for this gym.
    Owner can click any month to download the Excel file directly from R2.
    """
    from billing.services.monthly_report_store import (
        list_stored_reports,
        generate_and_store_monthly_report,
        get_report_url,
    )
    from django.utils import timezone

    gym    = _gym(request)
    today  = timezone.localdate()

    # Check if current month's report exists (it won't until the 1st runs)
    # and offer a manual trigger for the owner
    reports = list_stored_reports(gym)

    return render(request, 'billing/owner_report_list.html', {
        'gym':            gym,
        'owner_username': request.user.username,
        'reports':        reports,
        'today':          today,
    })


@_gym_owner_required
@require_GET
def owner_monthly_report_generate(request):
    """
    Manual trigger — owner can generate the current or previous month's
    report on demand if the cron hasn't run yet.
    Only generates if it doesn't exist; use ?force=1 to overwrite.
    """
    from billing.services.monthly_report_store import (
        generate_and_store_monthly_report,
        get_report_url,
    )
    from django.utils import timezone

    gym   = _gym(request)
    today = timezone.localdate()

    try:
        year  = int(request.GET.get('year',  today.year))
        month = int(request.GET.get('month', today.month))
        if not (1 <= month <= 12):
            raise ValueError
    except (ValueError, TypeError):
        year, month = today.year, today.month

    force = request.GET.get('force') == '1'

    if not force:
        existing = get_report_url(gym, year, month)
        if existing:
            return redirect(existing)

    try:
        url = generate_and_store_monthly_report(gym, year, month)
        return redirect(url)
    except Exception:
        logger.exception(
            "Manual report generation failed: gym=%s %d-%02d",
            gym.gym_code, year, month,
        )
        from django.contrib import messages
        messages.error(request, "Report generation failed. Please try again.")
        return redirect('owner:report_list')

# ──────────────────────────────────────────────────────────────────────────────
# 4. Invoice List
# ──────────────────────────────────────────────────────────────────────────────

@_gym_owner_required
@require_GET
def owner_invoice_list(request):
    """
    Paginated list of ALL invoices for this gym, sorted by invoice_number desc.
    Search by invoice number or member name via ?q=
    """
    from django.core.paginator import Paginator

    gym = _gym(request)
    q   = request.GET.get('q', '').strip()

    qs = (
        Invoice.objects
        .filter(gym=gym, status='issued')
        .select_related('member', 'member__selectPlan', 'related_payment')
        .order_by('-invoice_date', '-invoice_number')
    )

    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(invoice_number__icontains=q) |
            Q(customer_name__icontains=q)  |
            Q(customer_phone__icontains=q)
        )

    paginator = Paginator(qs, 25)
    page_obj  = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'billing/owner_invoice_list.html', {
        'gym':        gym,
        'owner_username': request.user.username,
        'page_obj':   page_obj,
        'query':      q,
        'total':      qs.count(),
    })


# ──────────────────────────────────────────────────────────────────────────────
# 5. Invoice PDF — owner-scoped redirect to Cloudflare R2
# ──────────────────────────────────────────────────────────────────────────────

@_gym_owner_required
@require_GET
def owner_invoice_pdf(request, pk):
    """
    Redirects to the Cloudflare R2 PDF URL for this invoice.
    If the PDF hasn't been generated yet, generates it on the fly.
    Only invoices belonging to this gym are accessible (get_object_or_404 enforces this).
    """
    gym     = _gym(request)
    invoice = get_object_or_404(Invoice, pk=pk, gym=gym, status='issued')

    if not invoice.pdf_url:
        try:
            generate_invoice_pdf(invoice)
        except Exception:
            logger.exception("Failed to generate PDF for invoice %s", invoice.invoice_number)
            return HttpResponse("PDF generation failed. Please try again.", status=500)

    # Redirect to Cloudflare R2 — PDF served directly from CDN, not proxied
    return redirect(invoice.pdf_url)
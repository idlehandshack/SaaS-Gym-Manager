from django.shortcuts import render

# Create your views here.
"""
billing/views.py
"""
import json
from datetime import date, datetime
from django.contrib.auth.decorators import login_required
from AuthFit.permissions import permission_required
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse ,Http404 ,HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST, require_GET
import logging
logger = logging.getLogger(__name__)
from billing.models import Invoice, Payment ,InvoiceShareToken
from billing.services.gst_report import generate_gstr1_style_report
from billing.services.invoice_generator import create_invoice_for_payment
from billing.services.pdf_generator import generate_invoice_pdf
from billing.services.invoice_share import (
    build_whatsapp_deep_link,
    InvalidPhoneNumber,
)
from django.core.paginator import Paginator
def _gym_from_request(request):
    """Pull the current gym off request (set by GymMiddleware)."""
    return getattr(request, 'gym', None)
from django.db.models import F, Window, Value, CharField
from django.db.models.functions import RowNumber, Coalesce, Cast
from django.db import connection
# ── Download / regenerate PDF ──────────────────────────────────────────────────

@login_required
@require_GET
def invoice_pdf_view(request, pk):
    """
    Returns the PDF for an invoice.
    If a cached Cloudinary URL exists — redirects there.
    Otherwise regenerates the PDF, uploads, then redirects.
    """
    from django.shortcuts import redirect

    gym     = _gym_from_request(request)
    invoice = get_object_or_404(Invoice, pk=pk, gym=gym)

    if not invoice.pdf_url:
        generate_invoice_pdf(invoice)

    return redirect(invoice.pdf_url)


# ── Regenerate PDF (force) ─────────────────────────────────────────────────────

@login_required
@require_GET
def invoice_pdf_regenerate_view(request, pk):
    gym     = _gym_from_request(request)
    invoice = get_object_or_404(Invoice, pk=pk, gym=gym)

    try:
        url = generate_invoice_pdf(invoice)
        return JsonResponse({'ok': True, 'pdf_url': url})
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


# ── GSTR-1 Export ─────────────────────────────────────────────────────────────

@login_required
@require_GET
def gstr1_export_view(request):
    """
    Query params:
        from  — YYYY-MM-DD  (default: start of current FY)
        to    — YYYY-MM-DD  (default: today)
    """
    gym = _gym_from_request(request)
    if gym is None:
        return HttpResponse('Gym not found', status=404)

    today = date.today()
    # Default: full current financial year
    if today.month >= 4:
        fy_start = date(today.year, 4, 1)
    else:
        fy_start = date(today.year - 1, 4, 1)

    try:
        start_date = datetime.strptime(request.GET.get('from', fy_start.isoformat()), '%Y-%m-%d').date()
        end_date   = datetime.strptime(request.GET.get('to',   today.isoformat()),    '%Y-%m-%d').date()
    except ValueError:
        return HttpResponse('Invalid date format. Use YYYY-MM-DD.', status=400)

    buf = generate_gstr1_style_report(gym, start_date, end_date)

    fy_label = f"{start_date.year}-{str(start_date.year + 1)[-2:]}"
    filename = f"GSTR1_{gym.gym_code}_{fy_label}.xlsx"

    response = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ── Record payment + create invoice ───────────────────────────────────────────

@login_required
@require_POST
def create_payment_view(request):
    """
    JSON POST body:
    {
        "enrollment_id": 123,
        "paid_amount": "1500.00",
        "payment_method": "U",       // C / U / B
        "payment_date": "2026-06-27" // optional, defaults to today
    }
    Returns: { "ok": true, "invoice_number": "INV/2026-27/0001", "pdf_url": "..." }
    """
    from AuthFit.models import Enrollment

    gym = _gym_from_request(request)
    if gym is None:
        return JsonResponse({'ok': False, 'error': 'Gym not found'}, status=404)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    enrollment_id = body.get('enrollment_id')
    paid_amount   = body.get('paid_amount')
    method        = body.get('payment_method', 'C')
    payment_date_str = body.get('payment_date', date.today().isoformat())

    if not enrollment_id or not paid_amount:
        return JsonResponse({'ok': False, 'error': 'enrollment_id and paid_amount are required'}, status=400)

    try:
        enrollment = Enrollment.objects.get(pk=enrollment_id, gym=gym)
    except Enrollment.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Enrollment not found'}, status=404)

    try:
        payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Invalid payment_date. Use YYYY-MM-DD.'}, status=400)

    from decimal import Decimal, InvalidOperation
    try:
        paid_decimal = Decimal(str(paid_amount))
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Invalid paid_amount'}, status=400)

    payment = Payment.objects.create(
        gym             = gym,
        enrollment      = enrollment,
        member_name     = enrollment.fullname,
        member_phone    = enrollment.phone,
        member_unique_id = enrollment.unique_id,
        plan_name       = enrollment.selectPlan.plan,
        plan_duration_days = enrollment.selectPlan.duration_days,
        amount          = enrollment.Amount,
        paid_amount     = paid_decimal,
        pending_amount  = max(Decimal('0'), enrollment.pendingAmount - paid_decimal),
        payment_method  = method,
        payment_date    = payment_date,
        membership_start = enrollment.doj,
        membership_end   = enrollment.DueDate,
    )

    invoice = create_invoice_for_payment(payment)

    # Generate PDF (synchronous — move to async if needed)
    try:
        generate_invoice_pdf(invoice)
    except Exception as exc:
        # PDF failure is non-fatal — invoice is still created
        pass

    return JsonResponse({
        'ok': True,
        'invoice_number': invoice.invoice_number,
        'pdf_url': invoice.pdf_url or '',
        'grand_total': str(invoice.grand_total),
    })

@login_required
@permission_required("can_refund_payment")
@require_POST
def issue_refund_view(request, invoice_pk):
    """
    POST /billing/invoice/<invoice_pk>/refund/
    Body (form-encoded): amount, reason, method (optional, defaults to 'original')
    """
    from billing.models import Invoice, Refund
    from billing.services.refund_service import issue_refund, RefundError

    gym = _gym_from_request(request)
    if gym is None:
        return JsonResponse({'ok': False, 'error': 'Gym not found'}, status=404)

    invoice = get_object_or_404(Invoice, pk=invoice_pk, gym=gym)

    amount = request.POST.get('amount', '').strip()
    reason = request.POST.get('reason', '').strip()
    method = request.POST.get('method', Refund.Method.ORIGINAL)

    if not amount:
        return JsonResponse({'ok': False, 'error': 'Refund amount is required.'}, status=400)

    try:
        refund = issue_refund(invoice, amount, reason, request.user, method=method, request=request)
    except RefundError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception("issue_refund_view failed for invoice_pk=%s", invoice_pk)
        return JsonResponse({'ok': False, 'error': 'Something went wrong. No changes were saved.'}, status=500)

    invoice.refresh_from_db()
    return JsonResponse({
        'ok': True,
        'refund_id': refund.id,
        'amount': str(refund.amount),
        'invoice_status': invoice.get_status_display(),
        'refundable_amount': str(invoice.refundable_amount),
    })

# Invoices eligible for sharing — exclude void/cancelled per spec's
# "deleted/invalid" exclusion. Draft is excluded too: nothing to send yet.
_SHAREABLE_STATUSES = [
    Invoice.Status.ISSUED,
    Invoice.Status.PAID,
    Invoice.Status.PARTIALLY_PAID,
    Invoice.Status.REFUNDED,
    Invoice.Status.PARTIALLY_REFUNDED,
]


def _gym_from_request(request):
    return getattr(request, 'gym', None)


# ──────────────────────────────────────────────────────────────────────────
# Staff-facing: list + search invoices to share
# ──────────────────────────────────────────────────────────────────────────
def _latest_paid_invoice_pks(gym):
    """
    Returns the PKs of the single latest (by invoice_date, then created_at)
    shareable/paid invoice per member/customer, computed entirely in the DB
    via ROW_NUMBER() OVER (PARTITION BY ...).

    Grouping key precedence:
        1. member_id            (registered member)
        2. customer_phone       (walk-in / non-member invoices)
        3. invoice id itself    (last-resort fallback so odd rows with
                                  neither never get collapsed together)
    """
    ranked = (
        Invoice.objects
        .filter(gym=gym, status__in=_SHAREABLE_STATUSES)
        .annotate(
            _group_key=Coalesce(
                Cast('member_id', output_field=CharField()),
                'customer_phone',
                Cast('id', output_field=CharField()),
                output_field=CharField(),
            ),
        )
        .annotate(
            _rn=Window(
                expression=RowNumber(),
                partition_by=[F('_group_key')],
                order_by=[F('invoice_date').desc(), F('created_at').desc()],
            ),
        )
        .values('id', '_rn')
    )

    # Window function results can't be filtered with .filter()/.exclude()
    # directly (SQL disallows referencing a window alias in WHERE at the
    # same query level), so we wrap the ranked query as a subquery and
    # filter rn = 1 in the outer SQL. Still 100% DB-side — no Invoice rows
    # are ever loaded into Python, only a flat list of ids.
    sql, params = ranked.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT id FROM ({sql}) AS ranked_invoices WHERE _rn = 1", params)
        return [row[0] for row in cursor.fetchall()]


@login_required
@permission_required("can_generate_invoice")
def send_invoice_page(request):
    gym = _gym_from_request(request)
    if gym is None:
        raise Http404("Gym not found")

    search = request.GET.get("search", "").strip()

    latest_pks = _latest_paid_invoice_pks(gym)

    qs = (
        Invoice.objects
        .filter(gym=gym, pk__in=latest_pks)
        .select_related("member", "related_payment")
        .order_by("-invoice_date", "-created_at")
    )

    # Optional date-range filters (from/to) — still apply cleanly on top of
    # the latest-per-member set since qs is just a normal filtered queryset.
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    if date_from:
        qs = qs.filter(invoice_date__gte=date_from)
    if date_to:
        qs = qs.filter(invoice_date__lte=date_to)

    if search:
        qs = qs.filter(models_q_search(search))

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(request, "billing/send_invoice.html", {
        "page_obj": page_obj,
        "search": search,
        "gym": gym,
    })


def models_q_search(search):
    from django.db.models import Q
    return (
        Q(invoice_number__icontains=search)
        | Q(customer_name__icontains=search)
        | Q(customer_phone__icontains=search)
    )


# ──────────────────────────────────────────────────────────────────────────
# Staff-facing: generate the WhatsApp link for one invoice (AJAX)
# ──────────────────────────────────────────────────────────────────────────
@login_required
@permission_required("can_generate_invoice")
@require_POST
def send_invoice_api(request):
    gym = _gym_from_request(request)
    if gym is None:
        return JsonResponse({"ok": False, "error": "Gym not found."}, status=404)

    invoice_pk = request.POST.get("invoice_id")
    if not invoice_pk:
        return JsonResponse({"ok": False, "error": "invoice_id is required."}, status=400)

    # Tenant-scoped lookup — never trust a client-supplied gym id, and a
    # mismatched invoice_pk simply 404s rather than leaking existence.
    try:
        invoice = Invoice.objects.select_related("gym", "member", "related_payment").get(
            pk=invoice_pk, gym=gym, status__in=_SHAREABLE_STATUSES
        )
    except (Invoice.DoesNotExist, ValueError):
        return JsonResponse({"ok": False, "error": "Invoice not found."}, status=404)

    try:
        link = build_whatsapp_deep_link(invoice, staff_user=request.user, request=request)
    except InvalidPhoneNumber as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception:
        logger.exception("send_invoice_api failed for invoice_pk=%s gym=%s", invoice_pk, gym.pk)
        return JsonResponse({"ok": False, "error": "Something went wrong generating the link."}, status=500)

    return JsonResponse({
        "ok": True,
        "wa_url": link.wa_url,
        "message": link.message,
        "phone": link.normalized_phone,
    })


# ──────────────────────────────────────────────────────────────────────────
# Public: view invoice by secure token (no login required)
# ──────────────────────────────────────────────────────────────────────────
@require_GET
def public_invoice_view(request, token):
    token_obj = get_object_or_404(
        InvoiceShareToken.objects.select_related(
            "invoice", "invoice__gym", "invoice__related_payment"
        ),
        token=token,
    )

    if token_obj.is_expired:
        return render(request, "billing/public_invoice_expired.html", status=410)

    invoice = token_obj.invoice
    if invoice.is_cancelled:
        # Void/cancelled invoices should not display, even with a valid token.
        raise Http404("Invoice not available.")

    token_obj.mark_opened()

    payment = getattr(invoice, "related_payment", None)

    response = render(request, "billing/public_invoice.html", {
        "invoice": invoice,
        "gym": invoice.gym,
        "payment": payment,
        "download_url": _reverse_download(token),
    })
    # Private financial data — never let a shared/proxy cache store this.
    response["Cache-Control"] = "private, no-store, max-age=0"
    return response


@require_GET
def public_invoice_download(request, token):
    token_obj = get_object_or_404(InvoiceShareToken.objects.select_related("invoice"), token=token)

    if token_obj.is_expired:
        raise Http404("Link expired.")

    invoice = token_obj.invoice
    if invoice.is_cancelled:
        raise Http404("Invoice not available.")

    # Reuse the existing PDF pipeline — never regenerate the invoice itself,
    # only lazily generate the PDF if it doesn't exist yet (mirrors
    # invoice_pdf_view's own behavior).
    if not invoice.pdf_url:
        generate_invoice_pdf(invoice)

    if not invoice.pdf_url:
        raise Http404("PDF not available.")

    return HttpResponseRedirect(invoice.pdf_url)


def _reverse_download(token):
    from django.urls import reverse
    return reverse("billing:public_invoice_download", kwargs={"token": token})
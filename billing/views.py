from django.shortcuts import render

# Create your views here.
"""
billing/views.py
-----------------
Minimal views wired into billing/urls.py.

  GET  /billing/invoice/<pk>/pdf/       — regenerate + download invoice PDF
  GET  /billing/gstr1/?from=YYYY-MM-DD&to=YYYY-MM-DD  — download GSTR-1 xlsx
  POST /billing/payment/create/         — record a payment & create invoice
"""
import json
from datetime import date, datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST, require_GET

from billing.models import Invoice, Payment
from billing.services.gst_report import generate_gstr1_style_report
from billing.services.invoice_generator import create_invoice_for_payment
from billing.services.pdf_generator import generate_invoice_pdf

def _gym_from_request(request):
    """Pull the current gym off request (set by GymMiddleware)."""
    return getattr(request, 'gym', None)


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

"""
billing/services/invoice_generator.py
---------------------------------------
Entry point for creating invoices.

Usage:
    from billing.services.invoice_generator import create_invoice_for_payment

    payment = Payment.objects.get(pk=payment_id)
    invoice = create_invoice_for_payment(payment)
    # invoice.pdf_url is populated after PDF generation
"""
from datetime import date
from decimal import Decimal

from django.db import transaction

from billing.models import Invoice, InvoiceLineItem, Payment
from billing.services.invoice_numbering import generate_invoice_number
from billing.services.tax_calculator import calculate_line_item_tax, round_off_amount


def _get_member_state(enrollment, fallback_state: str, fallback_code: str) -> tuple[str, str]:
    """
    Try to get the member's state from the Enrollment/User object.
    Members in EnterGYM don't currently store state separately, so we fall
    back to the gym's state (intrastate assumption — correct for most gym memberships
    since the member visits in person).
    """
    # If you later add state/state_code fields to Enrollment, read them here.
    # For now: assume intrastate (member is in same state as gym).
    return fallback_state, fallback_code


@transaction.atomic
def create_invoice_for_payment(payment: Payment) -> Invoice:
    """
    Creates an Invoice (+ line items) from a Payment record.
    Generates the invoice number, calculates tax, and saves everything atomically.
    PDF generation is intentionally NOT called here — trigger it async or in a view.

    Returns the saved Invoice instance.
    """
    gym         = payment.gym
    gst_profile = getattr(gym, 'gst_profile', None)
    enrollment  = payment.enrollment

    # ── Determine invoice type ────────────────────────────────────────────
    if gst_profile is None or not gst_profile.is_gst_registered or \
            (gst_profile and gst_profile.composition_scheme):
        invoice_type = Invoice.InvoiceType.BILL_OF_SUPPLY
    else:
        invoice_type = Invoice.InvoiceType.TAX_INVOICE

    # ── Invoice number ────────────────────────────────────────────────────
    invoice_number, fy = generate_invoice_number(gym, date.today())

    # ── Customer snapshot ─────────────────────────────────────────────────
    gym_state      = gst_profile.state      if gst_profile else ''
    gym_state_code = gst_profile.state_code if gst_profile else '00'

    customer_state, customer_state_code = _get_member_state(
        enrollment, gym_state, gym_state_code
    )

    # ── Tax calculation ───────────────────────────────────────────────────
    # Indian GST rate for gym / fitness services (SAC 999652) is 18%
    # If gym is not GST-registered → 0% (Bill of Supply)
    is_taxable = (invoice_type == Invoice.InvoiceType.TAX_INVOICE)
    gst_rate   = Decimal('18.00') if is_taxable else Decimal('0.00')

    sac_code   = gst_profile.default_sac_membership if gst_profile else '999652'

    taxable_value = Decimal(str(payment.paid_amount))   
    cgst, sgst, igst = calculate_line_item_tax(
        taxable_value, gst_rate, gym_state_code, customer_state_code
    )

    pre_roundoff_total = taxable_value + cgst + sgst + igst
    grand_total, round_off = round_off_amount(pre_roundoff_total)

    # ── Create Invoice ────────────────────────────────────────────────────
    invoice = Invoice.objects.create(
        gym            = gym,
        member         = enrollment,
        invoice_number = invoice_number,
        financial_year = fy,
        invoice_date   = date.today(),
        invoice_type   = invoice_type,

        customer_name       = payment.member_name,
        customer_phone      = payment.member_phone,
        customer_address    = enrollment.address if enrollment else '',
        customer_gstin      = '',
        customer_state      = customer_state,
        customer_state_code = customer_state_code,

        place_of_supply_state = customer_state,
        place_of_supply_code  = customer_state_code,

        taxable_value = taxable_value,
        cgst_amount   = cgst,
        sgst_amount   = sgst,
        igst_amount   = igst,
        round_off     = round_off,
        grand_total   = grand_total,

        related_payment = payment,
    )

    # ── Create line item ──────────────────────────────────────────────────
    InvoiceLineItem.objects.create(
        invoice      = invoice,
        description  = f"{payment.plan_name} membership",
        hsn_sac_code = sac_code,
        quantity     = Decimal('1'),
        unit_price   = taxable_value,
        taxable_value = taxable_value,
        gst_rate     = gst_rate,
        cgst_amount  = cgst,
        sgst_amount  = sgst,
        igst_amount  = igst,
    )

    return invoice


@transaction.atomic
def create_invoice_for_order(order) -> Invoice:
    """
    Creates an Invoice from a Shop.Order.
    Products are platform-global and not gym-GST-scoped, so we treat
    them as intrastate B2C (no customer GSTIN).
    """
    from billing.services.invoice_numbering import generate_invoice_number

    gym         = order.gym
    gst_profile = getattr(gym, 'gst_profile', None)

    is_registered  = gst_profile and gst_profile.is_gst_registered and \
                     not gst_profile.composition_scheme
    invoice_type   = Invoice.InvoiceType.TAX_INVOICE if is_registered \
                     else Invoice.InvoiceType.BILL_OF_SUPPLY

    invoice_number, fy = generate_invoice_number(gym, date.today())

    gym_state      = gst_profile.state      if gst_profile else ''
    gym_state_code = gst_profile.state_code if gst_profile else '00'
    gst_rate       = Decimal('18.00') if is_registered else Decimal('0.00')

    # For supplements/products the HSN code differs — use a generic one;
    # update per-product if needed.
    hsn_code = '21069099'   # Protein / health supplement HSN

    taxable_value = order.total_price
    cgst, sgst, igst = calculate_line_item_tax(
        taxable_value, gst_rate, gym_state_code, gym_state_code   # assume intrastate
    )
    pre_roundoff_total = taxable_value + cgst + sgst + igst
    grand_total, round_off = round_off_amount(pre_roundoff_total)

    customer_name = order.user.get_full_name() or order.user.username

    invoice = Invoice.objects.create(
        gym            = gym,
        invoice_number = invoice_number,
        financial_year = fy,
        invoice_date   = date.today(),
        invoice_type   = invoice_type,

        customer_name       = customer_name,
        customer_state      = gym_state,
        customer_state_code = gym_state_code,

        place_of_supply_state = gym_state,
        place_of_supply_code  = gym_state_code,

        taxable_value = taxable_value,
        cgst_amount   = cgst,
        sgst_amount   = sgst,
        igst_amount   = igst,
        round_off     = round_off,
        grand_total   = grand_total,

        related_order = order,
    )

    InvoiceLineItem.objects.create(
        invoice       = invoice,
        description   = f"{order.product.name}" + (f" – {order.flavor.name}" if order.flavor else ''),
        hsn_sac_code  = hsn_code,
        quantity      = Decimal(str(order.quantity)),
        unit_price    = order.total_price / order.quantity,
        taxable_value = taxable_value,
        gst_rate      = gst_rate,
        cgst_amount   = cgst,
        sgst_amount   = sgst,
        igst_amount   = igst,
    )

    return invoice

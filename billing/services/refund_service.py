#billing/services/refund_service.py
import logging
from decimal import Decimal

from django.db import transaction

from billing.models import Invoice, Refund

logger = logging.getLogger(__name__)


class RefundError(Exception):
    pass


@transaction.atomic
def issue_refund(invoice: Invoice, amount, reason: str, user, method: str = Refund.Method.ORIGINAL,
                  request=None) -> Refund:
    """
    Creates a Refund against `invoice`. Validates that:
      - the invoice is in a refundable state (not DRAFT, not already VOID/CANCELLED),
      - the amount doesn't exceed what's still refundable.

    Locks the Invoice row for the duration so two concurrent refund
    requests against the same invoice can't both succeed and together
    exceed grand_total.

    `request` is optional — pass it from a view to capture the staff
    member's IP in the audit log; omit it for programmatic/cron callers.

    Returns the created Refund. Raises RefundError on any validation failure —
    never partially applies a refund.
    """
    amount = Decimal(str(amount))

    if amount <= 0:
        raise RefundError("Refund amount must be greater than zero.")

    locked_invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)

    if locked_invoice.status in (Invoice.Status.DRAFT, Invoice.Status.VOID, Invoice.Status.CANCELLED):
        raise RefundError(
            f"Cannot refund an invoice with status '{locked_invoice.get_status_display()}'."
        )

    refundable = locked_invoice.refundable_amount
    if amount > refundable:
        raise RefundError(
            f"Refund amount ₹{amount} exceeds the refundable balance of ₹{refundable} "
            f"on invoice {locked_invoice.invoice_number}."
        )

    old_status = locked_invoice.status

    refund = Refund.objects.create(
        gym=locked_invoice.gym,
        invoice=locked_invoice,
        amount=amount,
        reason=reason,
        method=method,
        status=Refund.Status.COMPLETED,
        refunded_by=user,
    )

    # ── Status transition ────────────────────────────────────────────────
    if locked_invoice.is_fully_refunded:
        locked_invoice.status = Invoice.Status.REFUNDED
        locked_invoice.save(update_fields=['status', 'updated_at'])
    elif locked_invoice.refunded_amount > 0:
        # Partial refund — see Invoice.Status.PARTIALLY_REFUNDED (added below).
        locked_invoice.status = Invoice.Status.PARTIALLY_REFUNDED
        locked_invoice.save(update_fields=['status', 'updated_at'])

    from AuthFit.audit import log_action
    log_action(
        gym=locked_invoice.gym,
        action='refund_issued',
        staff_user=user,
        request=request,
        object_type='Invoice',
        object_id=locked_invoice.pk,
        object_label=locked_invoice.invoice_number,
        old_values={'status': old_status, 'refundable_amount': str(refundable)},
        new_values={'status': locked_invoice.status, 'refund_amount': str(amount), 'reason': reason},
    )

    logger.info(
        "Refund issued — invoice=%s amount=%s method=%s by=%s (fully_refunded=%s)",
        locked_invoice.invoice_number, amount, method,
        getattr(user, 'username', None), locked_invoice.is_fully_refunded,
    )

    return refund


def get_refund_history(gym, invoice=None):
    qs = Refund.objects.filter(gym=gym).select_related('invoice', 'refunded_by')
    if invoice is not None:
        qs = qs.filter(invoice=invoice)
    return qs.order_by('-refund_date', '-created_at')
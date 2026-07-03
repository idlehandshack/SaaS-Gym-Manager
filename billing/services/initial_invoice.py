import logging
from django.db import transaction
from django.utils import timezone

from billing.models import Payment
from billing.services.invoice_generator import create_invoice_for_payment
from billing.services.pdf_generator import generate_invoice_pdf

logger = logging.getLogger(__name__)


def ensure_initial_invoice(enrollment):
    """
    Idempotently create the Payment + Invoice for the payment collected
    up-front during Quick Enrollment (owner enters paidAmount directly on
    the Enrollment, bypassing Payment Management, so no Payment/Invoice
    exists yet).

    Cheap pre-checks avoid taking a lock on the common "nothing to do" path;
    the real guarantee comes from the row lock + re-checks inside the
    transaction, so concurrent calls (double-submitted Complete Profile,
    retries, etc.) can never create two Payments/Invoices for the same
    enrollment.
    """
    if enrollment.initial_invoice_generated:
        return None
    if not enrollment.user_id:
        return None
    if not enrollment.profile_completed:
        return None
    if not enrollment.paidAmount or enrollment.paidAmount <= 0:
        return None

    from AuthFit.models import Enrollment  # local import — avoid app-load cycles

    with transaction.atomic():
        locked = (
            Enrollment.objects
            .select_for_update()
            .get(pk=enrollment.pk)
        )

        # Re-check everything under the lock — state may have changed
        # between the pre-check above and acquiring the lock.
        if locked.initial_invoice_generated:
            return None
        if not locked.user_id or not locked.profile_completed:
            return None
        if not locked.paidAmount or locked.paidAmount <= 0:
            return None

        if Payment.objects.filter(enrollment=locked).exists():
            # Something else (e.g. staff via Payment Management) already
            # recorded a payment for this enrollment in the meantime.
            # Nothing to backfill — just mark it settled so we never retry.
            locked.initial_invoice_generated = True
            locked.save(update_fields=["initial_invoice_generated"])
            return None

        plan_price = (
            float(locked.selectPlan.price) if locked.selectPlan else float(locked.Amount)
        )

        payment = Payment.objects.create(
            gym=locked.gym,
            enrollment=locked,
            member_name=locked.fullname,
            member_phone=locked.phone,
            member_unique_id=locked.unique_id,
            plan_name=locked.selectPlan.plan if locked.selectPlan else '',
            plan_duration_days=locked.selectPlan.duration_days if locked.selectPlan else 30,
            amount=plan_price,
            paid_amount=locked.paidAmount,
            pending_amount=locked.pendingAmount,
            payment_method=locked.paymentMethod or None,
            payment_date=locked.paymentDate or locked.doj or timezone.localdate(),
            membership_start=locked.doj,
            membership_end=locked.DueDate,
        )

        invoice = create_invoice_for_payment(payment)
        try:
            generate_invoice_pdf(invoice)  # sets invoice.pdf_url internally, same as everywhere else
        except Exception:
            logger.exception(
                "Initial-invoice PDF generation failed — enrollment_id=%s invoice=%s",
                locked.id, invoice.invoice_number,
            )
            # Non-fatal, same convention as update_payment / create_payment_view —
            # the Invoice row exists even if the PDF didn't render.

        locked.initial_invoice_generated = True
        locked.save(update_fields=["initial_invoice_generated"])

        return invoice
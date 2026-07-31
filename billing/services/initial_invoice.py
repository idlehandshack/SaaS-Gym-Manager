import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from billing.models import Payment
from billing.services.invoice_generator import create_invoice_for_payment
from billing.services.pdf_generator import generate_invoice_pdf

logger = logging.getLogger(__name__)


def ensure_initial_invoice(enrollment):
    """
    Idempotently create the Payment + Invoice for whatever portion of
    Enrollment.paidAmount hasn't yet been invoiced.

    Handles both:
      - The simple case: Quick Enrollment collected money up-front, no
        Payment/Invoice exists yet at all.
      - The overlap case: staff recorded a top-up via Payment Management
        BEFORE profile completion — that creates a Payment for the delta
        only, so the original up-front amount is still uninvoiced and
        must be backfilled here, not skipped.

    Cheap pre-checks avoid taking a lock on the common "nothing to do" path;
    the real guarantee comes from the row lock + re-checks inside the
    transaction, so concurrent calls (double-submitted Complete Profile,
    retries, etc.) can never create two Payments/Invoices for the same
    uninvoiced amount.
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

        # ── The ONLY guard that decides how much to invoice ─────────────
        # Never short-circuit on "a Payment exists" alone — a top-up
        # Payment covers only its own delta, not the original up-front
        # amount. Always reconcile against the actual sum.
        existing_total = (
            Payment.objects.filter(enrollment=locked)
            .aggregate(total=Sum('paid_amount'))['total'] or 0
        )
        uninvoiced_amount = Decimal(str(locked.paidAmount)) - Decimal(str(existing_total))

        if uninvoiced_amount <= 0:
            # Fully covered already (e.g. a top-up Payment for >= the
            # up-front amount already exists) — nothing left to backfill.
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
            paid_amount=uninvoiced_amount,      # ← the actual gap, not locked.paidAmount
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
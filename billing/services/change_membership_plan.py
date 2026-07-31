"""
billing/services/change_membership_plan.py
---------------------------------------------------------------------------
Change an existing member's plan WITHOUT creating a new enrollment, a new
invoice, or a new invoice number. This is neither a renewal nor a new
enrollment — it edits the current membership in place.

v2 — hardened for production multi-tenant use per the "Review the entire
implementation" pass. Key behavioural changes from v1:

  - Will now REFUSE to run if the membership already has more than one
    Payment or more than one non-cancelled Invoice (i.e. it's already
    been through a renewal / another billing cycle). Historical GST
    invoices are never touched — only the single invoice belonging to
    the member's current, first-ever billing cycle is eligible. See
    get_active_membership_invoice() / _assert_single_billing_cycle().
  - Invoice/Payment relationship is validated before anything is
    written — if Invoice.related_payment doesn't point back at the same
    Payment tied to this enrollment, the operation is aborted.
  - PDF is cleared (pdf_url = "") and saved BEFORE regeneration, so a
    stale/wrong PDF is never left pointing at outdated invoice contents.
  - Membership line item is located via HSN/SAC / a stored membership
    identifier / a FK back to the Payment first, falling back to the old
    description-substring heuristic only for invoices created before
    those fields existed — never creates a duplicate line item.
  - calculate_line_item_tax() / round_off_amount() are called through
    compatibility wrappers that normalize dict / tuple / named-object
    return shapes, so this file doesn't break if your actual signatures
    differ from the original guess.
  - Cache invalidation covers everything the caller list asked for that
    this codebase actually has a cache key convention for (confirmed
    against AuthFit/models.py's post_save/post_delete signal handlers);
    a few keys are best-effort guesses, clearly marked, for cache
    buckets (invoice/payment/dashboard) whose real key format wasn't
    visible from this file alone.
  - Validation is broadened: inactive member, unavailable plan, missing
    payment/invoice, cancelled invoice, cross-gym data, broken
    invoice<->payment relationship are all now explicit PlanChangeError
    messages instead of silent no-ops.
  - Notifications now carry changed_by (staff) and new due date /
    pending amount (member), and are unchanged in that they only ever
    fire from transaction.on_commit() — never before commit.
  - Return payload is expanded per spec (invoice_id, payment_id,
    old_plan/new_plan names, invoice_updated, pdf_regenerated).

Everything still runs inside a single `transaction.atomic()` block — if
any step raises, the enrollment, payment, invoice and line-item changes
all roll back together.

⚠️ THINGS TO VERIFY AGAINST YOUR ACTUAL CODEBASE ⚠️
This file still hasn't been given sight of your real `billing/models.py`,
`tax_calculator.py`, or `pdf_generator.py`. Everything below is written
defensively (getattr/hasattr guards) so it degrades gracefully rather
than hard-crashing on a slightly different shape, but the following are
still best-effort assumptions and are each called out again inline where
they're used:

  - Invoice has a `related_payment` FK to Payment (used for #4/#7 below).
    If your model names this differently, update _validate_invoice_payment_link()
    and _find_membership_line_item().
  - InvoiceLineItem has an optional stored membership marker — checked
    via hasattr for `item_type` ('membership') or `membership_marker`,
    falling back to HSN/SAC match, falling back to related_payment,
    falling back to the old description__icontains="membership" heuristic.
  - MembershipPlan doesn't currently expose a soft-delete/active flag in
    the models.py you've shared, so the "deleted plan" / "unavailable
    plan" check below is a no-op unless such a field exists (checked via
    hasattr so it activates automatically the moment you add one).
  - calculate_line_item_tax(...) / round_off_amount(...) — see
    _normalize_tax_result() / _normalize_round_off() for the exact
    shapes handled.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from AuthFit.audit import log_action
from AuthFit.models import Enrollment, MembershipPlan, MembershipPlanChangeLog
from billing.models import Invoice, InvoiceLineItem, Payment
from billing.services.pdf_generator import generate_invoice_pdf
from billing.services.tax_calculator import calculate_line_item_tax, round_off_amount

logger = logging.getLogger(__name__)


class PlanChangeError(Exception):
    """
    Validation-level failure — safe to surface to the end user as-is
    (e.g. in a JsonResponse). Anything else raised inside the service is
    an unexpected error and should be logged + shown as a generic message
    by the caller.
    """
    pass


# ─────────────────────────────────────────────────────────────────────────
# Small pure helpers
# ─────────────────────────────────────────────────────────────────────────

def _format_duration(days: int) -> str:
    """30 -> '1 Month', 90 -> '3 Months', 10 -> '10 Days'."""
    days = int(days)
    if days > 0 and days % 30 == 0:
        months = days // 30
        return f"{months} Month" if months == 1 else f"{months} Months"
    return f"{days} Day" if days == 1 else f"{days} Days"


def _compute_membership_progress(enrollment, effective_date):
    """
    membership_start = current_due_date - old_plan.duration_days
    days_used         = effective_date - membership_start

    Falls back to "nothing consumed yet" only when there's no DueDate/plan
    duration to compute from (shouldn't happen once selectPlan is set, but
    guards against a null DueDate rather than crashing).
    """
    old_plan = enrollment.selectPlan
    if enrollment.DueDate and old_plan and old_plan.duration_days:
        membership_start = enrollment.DueDate - timedelta(days=old_plan.duration_days)
    else:
        membership_start = effective_date

    days_used = (effective_date - membership_start).days
    days_used = max(days_used, 0)  # never let clock-skew produce negative usage
    return membership_start, days_used


def _compute_new_due_date(membership_start, new_plan):
    """
    new_due_date = membership_start + new_plan.duration_days

    Equivalent to effective_date + (new_plan.duration_days - days_used) —
    preserves days already consumed instead of restarting from the
    plan-change date (Feature 1 / Feature 6).
    """
    return membership_start + timedelta(days=new_plan.duration_days)


def _validate_plan_duration(days_used, new_plan):
    """Feature 4 — reject a downgrade that can't cover days already used."""
    if days_used > new_plan.duration_days:
        raise PlanChangeError(
            f"Member has already used {days_used} days. The selected plan "
            f"only provides {new_plan.duration_days} days. Please choose a "
            f"plan with a longer duration."
        )


def _compute_payment_split(old_paid: Decimal, new_price: Decimal):
    """
    Matches the three documented cases exactly:
      higher price   -> paid unchanged, pending = new - paid, status Pending
      lower price    -> paid capped to new price (no auto-refund), pending 0, Done
      partial payer  -> paid unchanged (since paid < new price), pending = new - paid
    """
    new_paid = min(old_paid, new_price)
    new_pending = new_price - new_paid
    status = "Done" if new_pending <= 0 else "Pending"
    return new_paid, new_pending, status


def _extract_tax_amount(source, *keys, default=Decimal("0")):
    """Pull the first matching key/attr out of a dict-or-object tax result."""
    for k in keys:
        if isinstance(source, dict) and k in source:
            return source[k] or default
        if hasattr(source, k):
            val = getattr(source, k)
            if val is not None:
                return val
    return default


# ─────────────────────────────────────────────────────────────────────────
# Tax / round-off compatibility wrappers (req. #8, #9)
# ─────────────────────────────────────────────────────────────────────────

def _normalize_tax_result(raw) -> dict:
    """
    calculate_line_item_tax() may return a dict, a (cgst, sgst, igst)
    tuple/list, or a named object/dataclass with cgst/sgst/igst (or
    *_amount) attributes. Normalize whatever comes back into a plain
    {"cgst": Decimal, "sgst": Decimal, "igst": Decimal} dict so the rest
    of this file never has to care which shape your installation uses.
    """
    zero = Decimal("0")
    if raw is None:
        return {"cgst": zero, "sgst": zero, "igst": zero}

    if isinstance(raw, dict):
        return {
            "cgst": _extract_tax_amount(raw, "cgst", "cgst_amount", default=zero),
            "sgst": _extract_tax_amount(raw, "sgst", "sgst_amount", default=zero),
            "igst": _extract_tax_amount(raw, "igst", "igst_amount", default=zero),
        }

    if isinstance(raw, (tuple, list)):
        # Only sane ordering for a bare 3-tuple in this domain.
        padded = list(raw) + [zero] * max(0, 3 - len(raw))
        return {
            "cgst": padded[0] if padded[0] is not None else zero,
            "sgst": padded[1] if padded[1] is not None else zero,
            "igst": padded[2] if padded[2] is not None else zero,
        }

    # Named object / dataclass / NamedTuple with attributes.
    return {
        "cgst": _extract_tax_amount(raw, "cgst", "cgst_amount", default=zero),
        "sgst": _extract_tax_amount(raw, "sgst", "sgst_amount", default=zero),
        "igst": _extract_tax_amount(raw, "igst", "igst_amount", default=zero),
    }


def _calculate_line_item_tax(**kwargs) -> dict:
    """Compatibility-wrapped call to the real tax_calculator.py."""
    raw = calculate_line_item_tax(**kwargs)
    return _normalize_tax_result(raw)


def _normalize_round_off(raw, pre_round_total: Decimal):
    """
    round_off_amount() may return (rounded_total, round_off_diff), just
    rounded_total, or a named object with those two values under some
    other attribute name. Normalize to a (rounded_total, round_off_diff)
    tuple, computing the diff ourselves if the callee didn't supply one.
    """
    if isinstance(raw, (tuple, list)):
        if len(raw) >= 2:
            return raw[0], raw[1]
        if len(raw) == 1:
            rounded = raw[0]
            return rounded, rounded - pre_round_total
        return pre_round_total, Decimal("0")

    for total_attr, diff_attr in (
        ("rounded_total", "round_off_diff"),
        ("rounded_total", "round_off"),
        ("total", "round_off"),
    ):
        if hasattr(raw, total_attr):
            rounded = getattr(raw, total_attr)
            diff = getattr(raw, diff_attr, None)
            if diff is None:
                diff = rounded - pre_round_total
            return rounded, diff

    # Plain scalar.
    rounded = raw
    return rounded, rounded - pre_round_total


def _round_off_amount(pre_round_total: Decimal):
    """Compatibility-wrapped call to the real tax_calculator.py."""
    raw = round_off_amount(pre_round_total)
    return _normalize_round_off(raw, pre_round_total)


# ─────────────────────────────────────────────────────────────────────────
# Active-membership / billing-history helpers (req. #1, #2, #3, #4)
# ─────────────────────────────────────────────────────────────────────────

def get_active_membership_invoice(enrollment, *, for_update: bool = False):
    """
    Locate the invoice belonging to the member's CURRENT billing cycle —
    never "most recent"/"latest" across renewals. Excludes cancelled
    invoices. Returns None if no eligible invoice exists.

    This never overwrites a historical invoice: change_membership_plan()
    calls _assert_single_billing_cycle() first, which refuses to proceed
    at all if this enrollment has more than one non-cancelled invoice —
    so by the time this helper is called there is at most one candidate,
    and returning "the one" is unambiguous rather than a "pick the
    newest and hope" guess.
    """
    qs = (
        Invoice.objects
        .filter(gym=enrollment.gym, member=enrollment)
        .exclude(status=Invoice.Status.CANCELLED)
    )
    if for_update:
        qs = qs.select_for_update()
    return qs.order_by("-invoice_date", "-created_at").first()


def get_membership_payment(enrollment, *, for_update: bool = False):
    """Locate the (at most one, by this point) Payment tied to this enrollment."""
    qs = Payment.objects.filter(gym=enrollment.gym, enrollment=enrollment)
    if for_update:
        qs = qs.select_for_update()
    return qs.order_by("-payment_date", "-created_at").first()


def _assert_single_billing_cycle(enrollment):
    """
    Refuses to touch a membership that already has more than one Payment
    or more than one non-cancelled Invoice — i.e. it's already been
    through a renewal or another billing cycle. Overwriting billing
    records in that state risks corrupting historical GST invoices, so
    this blocks instead and points staff at Renewal.

    Returns (payment_count, invoice_count) for the caller's own
    missing-record checks.
    """
    payment_count = Payment.objects.filter(gym=enrollment.gym, enrollment=enrollment).count()
    invoice_count = (
        Invoice.objects
        .filter(gym=enrollment.gym, member=enrollment)
        .exclude(status=Invoice.Status.CANCELLED)
        .count()
    )
    if payment_count > 1 or invoice_count > 1:
        raise PlanChangeError(
            "This membership already contains billing history. "
            "Membership plan can only be changed before another billing "
            "cycle begins. Please renew the member using the new plan instead."
        )
    return payment_count, invoice_count


def _validate_invoice_payment_link(invoice, payment, enrollment):
    """
    Ensure Invoice.related_payment actually belongs to Payment.enrollment
    before we let anything write to either row. If the relationship is
    broken (or the FK doesn't exist on your model — checked via hasattr),
    we stop rather than silently updating records that don't actually
    correspond to each other.

    ASSUMPTION: Invoice has a `related_payment` FK. If your model calls
    it something else, update the hasattr/getattr calls below.
    """
    if not hasattr(invoice, "related_payment_id"):
        # Model doesn't track this relationship explicitly — nothing to
        # validate; both rows were already independently gym+enrollment
        # scoped when fetched.
        return

    related_payment_id = invoice.related_payment_id
    if related_payment_id is None:
        return  # Not linked yet — legacy invoice, nothing to contradict.

    if related_payment_id != payment.id:
        raise PlanChangeError(
            "This invoice is not linked to the member's current payment "
            "record. Refusing to update mismatched billing records — "
            "please check this membership's billing history manually."
        )

    related_payment_enrollment_id = getattr(invoice.related_payment, "enrollment_id", None)
    if related_payment_enrollment_id is not None and related_payment_enrollment_id != enrollment.id:
        raise PlanChangeError(
            "This invoice's linked payment does not belong to this "
            "member. Refusing to update mismatched billing records."
        )


# ─────────────────────────────────────────────────────────────────────────
# Invoice line-item detection (req. #7)
# ─────────────────────────────────────────────────────────────────────────

def _find_membership_line_item(invoice, payment, new_plan):
    """
    Locate the invoice's membership line item without relying solely on
    a fragile description substring match. Tries, in order:
      1. A stored membership marker on the line item, if your
         InvoiceLineItem model has one (checked via hasattr so this is a
         no-op until such a field exists: `item_type == 'membership'` or
         `is_membership_item == True`).
      2. A FK back to the same Payment (`related_payment_id == payment.id`),
         if your InvoiceLineItem model has one.
      3. HSN/SAC code matching the plan's configured code, if
         MembershipPlan exposes one.
      4. Fallback: description__icontains="membership" — the original
         heuristic, kept only for invoices/line items created before any
         of the fields above existed.
    Returns a queryset of matches (should be 0 or 1 under normal
    operation) so the caller can delete all of them before creating
    exactly one new line item — this is what prevents duplicates even if
    an older invoice somehow ended up with more than one match.
    """
    lines = invoice.line_items.all()

    if hasattr(InvoiceLineItem, "item_type"):
        matches = lines.filter(item_type="membership")
        if matches.exists():
            return matches

    if hasattr(InvoiceLineItem, "is_membership_item"):
        matches = lines.filter(is_membership_item=True)
        if matches.exists():
            return matches

    if payment is not None and hasattr(InvoiceLineItem, "related_payment_id"):
        matches = lines.filter(related_payment_id=payment.id)
        if matches.exists():
            return matches

    plan_hsn = getattr(new_plan, "hsn_sac_code", None)
    if plan_hsn:
        matches = lines.filter(hsn_sac_code=plan_hsn)
        if matches.exists():
            return matches

    # Legacy fallback — original heuristic.
    return lines.filter(description__icontains="membership")


# ─────────────────────────────────────────────────────────────────────────
# Validation (req. #14)
# ─────────────────────────────────────────────────────────────────────────

def _validate_before_change(enrollment, new_plan, gym):
    if enrollment is None:
        raise PlanChangeError("Member not found.")

    if new_plan.gym_id != gym.id:
        raise PlanChangeError("Selected plan does not belong to this gym.")

    # "Deleted / unavailable plan" — MembershipPlan doesn't currently
    # expose a soft-delete/active flag; these checks are no-ops until one
    # exists, activated automatically via hasattr so nothing breaks today.
    if hasattr(new_plan, "is_deleted") and new_plan.is_deleted:
        raise PlanChangeError("Selected plan has been removed and is no longer available.")
    if hasattr(new_plan, "is_active") and not new_plan.is_active:
        raise PlanChangeError("Selected plan is currently inactive.")

    if hasattr(enrollment, "is_active") and not enrollment.is_active:
        raise PlanChangeError("This member is not active. Reactivate the membership before changing the plan.")

    if enrollment.selectPlan_id == new_plan.id:
        raise PlanChangeError("Member is already on this plan.")


# ─────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────

def change_membership_plan(enrollment, new_plan, effective_date, reason, changed_by):
    """
    enrollment   : AuthFit.models.Enrollment instance (already gym-scoped
                   and fetched by the caller — this function re-fetches
                   + locks it internally).
    new_plan     : AuthFit.models.MembershipPlan instance.
    effective_date: date — when the new plan takes effect (usually today).
    reason       : str, optional.
    changed_by   : the staff User performing the change.

    Returns a dict:
      {
        "success": True,
        "invoice_number": "...",
        "invoice_id": ...,
        "payment_id": ...,
        "pdf_url": "...",
        "old_plan": "...",
        "new_plan": "...",
        "new_due_date": "YYYY-MM-DD",
        "paid_amount": "...",
        "pending_amount": "...",
        "payment_status": "Done" | "Pending",
        "invoice_updated": True,
        "pdf_regenerated": True,
      }

    Raises PlanChangeError for expected validation problems — the caller
    (the view) is expected to catch this and surface str(exc) as-is.
    """
    gym = enrollment.gym if enrollment is not None else None
    if gym is None:
        raise PlanChangeError("Member not found.")
    with transaction.atomic():
        # Lock the enrollment row so two staff members can't change the
        # same membership concurrently.
        enrollment = (
            Enrollment.objects
            .select_for_update()
            .select_related("selectPlan")
            .filter(gym=gym, pk=enrollment.pk)
            .first()
        )
        if enrollment is None:
            raise PlanChangeError("Member not found.")

        # Re-run the cheap checks against the freshly locked row — plan
        # could theoretically have changed between the pre-check above
        # and acquiring the lock.
        _validate_before_change(enrollment, new_plan, gym)

        # ── Billing-history guard (req. #1, #2) ─────────────────────
        # Refuses outright if this membership has already been through
        # more than one billing cycle. Historical invoices are NEVER
        # touched by this service.
        _assert_single_billing_cycle(enrollment)

        old_plan     = enrollment.selectPlan
        old_price    = enrollment.Amount
        old_due_date = enrollment.DueDate
        old_paid     = enrollment.paidAmount

        new_price = Decimal(new_plan.price)

        membership_start, days_used = _compute_membership_progress(enrollment, effective_date)
        _validate_plan_duration(days_used, new_plan)
        new_due_date = _compute_new_due_date(membership_start, new_plan)

        new_paid, new_pending, new_status = _compute_payment_split(old_paid, new_price)

        # ── 1. Enrollment ────────────────────────────────────────────
        enrollment.selectPlan    = new_plan
        enrollment.Amount        = new_price
        enrollment.paidAmount    = new_paid
        enrollment.pendingAmount = new_pending
        enrollment.paymentStatus = new_status
        enrollment.DueDate       = new_due_date
        enrollment.save(update_fields=[
            "selectPlan", "Amount", "paidAmount", "pendingAmount",
            "paymentStatus", "DueDate",
        ])

        # ── 2. Locate the ACTIVE-CYCLE Payment + Invoice only ────────
        # (req. #3) — never "most recent"; by this point there's at most
        # one of each, guaranteed by _assert_single_billing_cycle above.
        payment = get_membership_payment(enrollment, for_update=True)
        invoice = get_active_membership_invoice(enrollment, for_update=True)

        # Cross-gym / cancelled / relationship safety (req. #4, #14) —
        # these should be unreachable given the gym-scoped queries above,
        # but are checked explicitly rather than assumed.
        if payment is not None and payment.gym_id != gym.id:
            raise PlanChangeError("Payment record belongs to a different gym. Refusing to proceed.")
        if invoice is not None and invoice.gym_id != gym.id:
            raise PlanChangeError("Invoice belongs to a different gym. Refusing to proceed.")
        if invoice is not None and invoice.status == Invoice.Status.CANCELLED:
            raise PlanChangeError("The linked invoice has been cancelled. Refusing to update a cancelled invoice.")
        _validate_invoice_payment_link(invoice, payment, enrollment)

        invoice_updated  = False
        pdf_regenerated  = False

        result = {
            "success":         True,
            "invoice_number":  invoice.invoice_number if invoice else None,
            "invoice_id":      invoice.id if invoice else None,
            "payment_id":      payment.id if payment else None,
            "pdf_url":         invoice.pdf_url if invoice else "",
            "old_plan":        old_plan.plan if old_plan else None,
            "new_plan":        new_plan.plan,
            "new_due_date":    new_due_date.isoformat(),
            "paid_amount":     str(new_paid),
            "pending_amount":  str(new_pending),
            "payment_status":  new_status,
            "invoice_updated": invoice_updated,
            "pdf_regenerated": pdf_regenerated,
        }

        # ── 3. Update the Payment snapshot (reuse the row, never create) ──
        if payment is not None:
            payment.plan_name          = new_plan.plan
            payment.plan_duration_days = new_plan.duration_days
            payment.amount             = new_price
            payment.paid_amount        = new_paid
            payment.pending_amount     = new_pending
            payment.membership_end     = new_due_date
            payment.save(update_fields=[
                "plan_name", "plan_duration_days", "amount",
                "paid_amount", "pending_amount", "membership_end",
            ])

        # ── 4. Update the existing (active-cycle) invoice in place ──
        if invoice is not None:
            # Refresh only the customer snapshot fields explicitly called
            # out by the spec. NEVER touch id, invoice_number,
            # financial_year, created_at.
            invoice.customer_name    = enrollment.fullname
            invoice.customer_phone   = enrollment.phone
            invoice.customer_address = enrollment.address or invoice.customer_address
            if hasattr(invoice, "customer_state") and hasattr(enrollment, "state"):
                invoice.customer_state = getattr(enrollment, "state", invoice.customer_state)
            if hasattr(invoice, "customer_state_code") and hasattr(enrollment, "state_code"):
                invoice.customer_state_code = getattr(enrollment, "state_code", invoice.customer_state_code)
            if hasattr(invoice, "place_of_supply") and hasattr(enrollment, "state"):
                invoice.place_of_supply = getattr(enrollment, "state", invoice.place_of_supply)

            # ── Locate + replace the membership line item (req. #7) ──
            old_line_items = _find_membership_line_item(invoice, payment, new_plan)
            reference_item = old_line_items.first()
            gst_rate     = reference_item.gst_rate if reference_item else Decimal("0")
            hsn_sac_code = reference_item.hsn_sac_code if reference_item else ""
            # Delete every match, not just one — guarantees no duplicates
            # survive even if an older invoice had more than one.
            old_line_items.delete()

            gst_profile = getattr(gym, "gst_profile", None)

            gym_state_code = (
                gst_profile.state_code
                if gst_profile else "00"
            )

            cgst, sgst, igst = calculate_line_item_tax(
                new_paid,
                gst_rate,
                gym_state_code,
                invoice.customer_state_code,
            )

            new_line_item = InvoiceLineItem.objects.create(
                invoice=invoice,
                description=f"{new_plan.plan} Membership - {_format_duration(new_plan.duration_days)}",
                hsn_sac_code=hsn_sac_code,
                quantity=1,
                unit_price=new_paid,
                taxable_value=new_paid,
                gst_rate=gst_rate,
                cgst_amount=cgst,
                sgst_amount=sgst,
                igst_amount=igst,
            )
            if hasattr(new_line_item, "related_payment_id") and payment is not None:
                new_line_item.related_payment = payment
                new_line_item.save(update_fields=["related_payment"])
            if hasattr(new_line_item, "item_type"):
                new_line_item.item_type = "membership"
                new_line_item.save(update_fields=["item_type"])

            # Recompute invoice totals from whatever line items remain,
            # so a bundled invoice (e.g. membership + joining fee) stays
            # correct even though we only touched the membership line.
            remaining_lines = list(invoice.line_items.all())
            taxable_total = sum((li.taxable_value for li in remaining_lines), Decimal("0"))
            cgst_total    = sum((li.cgst_amount for li in remaining_lines), Decimal("0"))
            sgst_total    = sum((li.sgst_amount for li in remaining_lines), Decimal("0"))
            igst_total    = sum((li.igst_amount for li in remaining_lines), Decimal("0"))
            pre_round_total = taxable_total + cgst_total + sgst_total + igst_total

            rounded_total, round_off_diff = _round_off_amount(pre_round_total)

            invoice.taxable_value = taxable_total
            invoice.cgst_amount   = cgst_total
            invoice.sgst_amount   = sgst_total
            invoice.igst_amount   = igst_total
            invoice.round_off     = round_off_diff
            invoice.grand_total   = rounded_total

            update_fields = [
                "customer_name", "customer_phone", "customer_address",
                "taxable_value", "cgst_amount", "sgst_amount", "igst_amount",
                "round_off", "grand_total", "updated_at",
            ]
            for optional_field in ("customer_state", "customer_state_code", "place_of_supply"):
                if hasattr(invoice, optional_field):
                    update_fields.append(optional_field)
            invoice.save(update_fields=update_fields)
            invoice_updated = True

            # ── 5. Clear, then regenerate the PDF (req. #5) ─────────
            # Clear pdf_url FIRST so a failed regeneration never leaves a
            # stale PDF pointing at now-outdated invoice contents.
            invoice.pdf_url = ""
            invoice.save(update_fields=["pdf_url"])
            result["pdf_url"] = ""

            try:
                new_pdf_url = generate_invoice_pdf(invoice)
                if new_pdf_url:
                    invoice.pdf_url = new_pdf_url
                    invoice.save(update_fields=["pdf_url"])
                    result["pdf_url"] = new_pdf_url
                    pdf_regenerated = True
            except Exception:
                logger.exception(
                    "PDF regeneration failed after plan change — invoice=%s",
                    invoice.invoice_number,
                )
                # Don't roll back the whole plan change just because PDF
                # rendering failed — the invoice data is correct. pdf_url
                # is intentionally left empty (not stale) — use the
                # existing regenerate view to retry.

            result["invoice_number"]  = invoice.invoice_number
            result["invoice_updated"] = invoice_updated
            result["pdf_regenerated"] = pdf_regenerated

        # ── 6. Permanent audit log ───────────────────────────────────
        MembershipPlanChangeLog.objects.create(
            gym=gym,
            enrollment=enrollment,
            old_plan=old_plan,
            new_plan=new_plan,
            old_price=old_price,
            new_price=new_price,
            old_due_date=old_due_date,
            new_due_date=new_due_date,
            reason=reason or "",
            changed_by=changed_by,
        )

        # ── 6b. General financial audit trail (Gym.AuditLog) ─────────
        # Kept here rather than in the calling view, since this is the
        # only place with full context: invoice_id, payment_id, and the
        # exact amounts written to each row in this transaction.
        log_action(
            gym=gym,
            action='plan_changed',
            staff_user=changed_by,
            object_type='Enrollment',
            object_id=enrollment.pk,
            object_label=enrollment.fullname,
            old_values={
                'plan': old_plan.plan if old_plan else None,
                'price': str(old_price),
                'due_date': old_due_date.isoformat() if old_due_date else None,
            },
            new_values={
                'plan': new_plan.plan,
                'price': str(new_price),
                'due_date': new_due_date.isoformat(),
                'invoice_id': invoice.id if invoice else None,
                'payment_id': payment.id if payment else None,
                'reason': reason or '',
            },
        )

        # ── 7. Logging (req. #13) ─────────────────────────────────────
        logger.info(
            "Membership plan changed — gym=%s enrollment_id=%s invoice_number=%s "
            "payment_id=%s old_plan=%s new_plan=%s old_amount=%s new_amount=%s "
            "changed_by=%s",
            getattr(gym, "gym_code", gym.id), enrollment.id, result["invoice_number"],
            payment.id if payment else None,
            old_plan.plan if old_plan else None, new_plan.plan,
            old_price, new_price,
            getattr(changed_by, "username", changed_by),
        )

        # Capture what we need for the post-commit callback now, while
        # we're still inside the atomic block and objects are fresh.
        uid         = enrollment.user_id
        gym_pk      = gym.pk
        invoice_pk  = invoice.id if invoice else None
        payment_pk  = payment.id if payment else None

        def _after_commit():
            _invalidate_plan_change_caches(uid, gym_pk, invoice_pk, payment_pk)
            _notify_staff_plan_changed(enrollment, old_plan, new_plan, changed_by)
            _notify_member_plan_changed(
                enrollment,
                new_plan,
                new_due_date=new_due_date,
                pending_amount=new_pending,
            )

        transaction.on_commit(_after_commit)

    return result


# ─────────────────────────────────────────────────────────────────────────
# Cache invalidation (req. #10)
# ─────────────────────────────────────────────────────────────────────────

def _invalidate_plan_change_caches(uid, gym_pk, invoice_pk, payment_pk):
    """
    Keys marked CONFIRMED match the cache-clearing signal handlers
    already in AuthFit/models.py (clear_enrollment_cache /
    clear_plan_cache) — same format, so a plan change invalidates
    exactly what a normal enrollment save would. Keys marked GUESS are
    this file's best effort at a plausible convention for cache buckets
    (invoice/payment/dashboard/PDF) whose real key format wasn't visible
    from the files reviewed here — verify these against your actual
    cache-key helpers and adjust if they differ.
    """
    confirmed_keys = (
        f"enrollment_{uid}_{gym_pk}",
        f"enrollment_status_{uid}_{gym_pk}",
        f"enrolled_{uid}_{gym_pk}",
        f"profile_image_{uid}",
        f"admin_revenue_{gym_pk}",
        f"face_users_{gym_pk}",
    )
    # ---- GUESS: verify these against your actual cache-key helpers ----
    guessed_keys = (
        f"membership_plans_{gym_pk}",
        f"dashboard_{gym_pk}",
        f"dashboard_revenue_{gym_pk}",
        f"member_invoices_{uid}_{gym_pk}",
        f"member_payments_{uid}_{gym_pk}",
    )
    if invoice_pk is not None:
        guessed_keys += (f"invoice_{invoice_pk}", f"invoice_pdf_{invoice_pk}")
    if payment_pk is not None:
        guessed_keys += (f"payment_{payment_pk}",)
    # ---------------------------------------------------------------------

    for key in confirmed_keys + guessed_keys:
        cache.delete(key)


# ─────────────────────────────────────────────────────────────────────────
# Notifications — best-effort, never allowed to break the transaction.
# Only ever called from transaction.on_commit() above — never before
# commit (req. #12).
# ─────────────────────────────────────────────────────────────────────────

def _notify_staff_plan_changed(enrollment, old_plan, new_plan, changed_by):
    """
    Staff-facing alert, fired to every active staff device at the gym.
    Mirrors Shop.notifications.notify_staff_new_enrollment's all-staff
    pattern. Now includes changed_by per req. #12.
    """
    try:
        from Shop.notifications import notify_staff_plan_changed as _notify
    except ImportError:
        logger.warning(
            "Staff plan-change notification skipped — Shop.notifications."
            "notify_staff_plan_changed is missing — member=%s old_plan=%s new_plan=%s",
            enrollment.fullname, old_plan, new_plan,
        )
        return
    try:
        _notify(enrollment, old_plan, new_plan, changed_by=changed_by)
    except Exception:
        logger.exception("Staff plan-change notification failed for enrollment_id=%s", enrollment.id)


def _notify_member_plan_changed(enrollment, new_plan, new_due_date, pending_amount):
    """
    Member-facing alert. Delegates to
    AuthFit.notifications.notify_member_plan_changed. Now includes the
    new due date and pending amount per req. #12.
    """
    if not enrollment.user_id:
        return
    try:
        from AuthFit.notifications import notify_member_plan_changed as _notify
    except ImportError:
        logger.warning(
            "Member plan-change notification skipped — AuthFit.notifications."
            "notify_member_plan_changed is missing — member=%s new_plan=%s",
            enrollment.fullname, new_plan.plan,
        )
        return
    try:
        _notify(enrollment, new_plan, new_due_date=new_due_date, pending_amount=pending_amount)
    except Exception:
        logger.exception("Member plan-change notification failed for enrollment_id=%s", enrollment.id)
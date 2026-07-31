from django.db import models
from django.utils import timezone
from django.core.cache import cache
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from decimal import Decimal
# ──────────────────────────────────────────────────────────────────────────────
# Payment
# Thin snapshot of a membership payment. Created when staff records a payment
# on an Enrollment. Invoice is then generated from this.
# ──────────────────────────────────────────────────────────────────────────────
class Payment(models.Model):
    class Method(models.TextChoices):
        CASH     = 'C', 'Cash'
        UPI      = 'U', 'UPI'
        UPI_CASH = 'B', 'UPI + Cash'

    gym        = models.ForeignKey('Gym.Gym', on_delete=models.CASCADE,
                                   related_name='payments', db_index=True)
    enrollment = models.ForeignKey('AuthFit.Enrollment', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='payments')

    # Snapshot — kept even if enrollment is deleted later
    member_name    = models.CharField(max_length=100)
    member_phone   = models.CharField(max_length=10)
    member_unique_id = models.CharField(max_length=10)
    plan_name      = models.CharField(max_length=100)
    plan_duration_days = models.PositiveIntegerField(default=30)

    amount          = models.DecimalField(max_digits=10, decimal_places=2)
    paid_amount     = models.DecimalField(max_digits=10, decimal_places=2)
    pending_amount  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_method  = models.CharField(max_length=1, choices=Method.choices,
                                       blank=True, null=True)
    payment_date    = models.DateField()

    membership_start = models.DateField()
    membership_end   = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date', '-created_at']
        indexes  = [models.Index(fields=['gym', 'payment_date'])]

    def __str__(self):
        return f"{self.member_name} — ₹{self.paid_amount} on {self.payment_date}"


# ──────────────────────────────────────────────────────────────────────────────
# Invoice counter — one row per gym per financial year
# Lives here so Django auto-discovers it. DO NOT move to services/.
# ──────────────────────────────────────────────────────────────────────────────
class InvoiceCounter(models.Model):
    gym            = models.ForeignKey('Gym.Gym', on_delete=models.CASCADE,
                                       related_name='invoice_counters')
    financial_year = models.CharField(max_length=7)   # e.g. "2026-27"
    last_number    = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['gym', 'financial_year'],
                name='unique_counter_per_gym_fy'
            )
        ]
        verbose_name        = 'Invoice Counter'
        verbose_name_plural = 'Invoice Counters'

    def __str__(self):
        return f"{self.gym.gym_code} / {self.financial_year} → #{self.last_number}"


# ──────────────────────────────────────────────────────────────────────────────
# Invoice
# ──────────────────────────────────────────────────────────────────────────────
class Invoice(models.Model):
    class InvoiceType(models.TextChoices):
        TAX_INVOICE    = 'tax_invoice',    'Tax Invoice'
        BILL_OF_SUPPLY = 'bill_of_supply', 'Bill of Supply'

    class Status(models.TextChoices):
        DRAFT               = 'draft',               'Draft'
        ISSUED              = 'issued',               'Issued'
        PAID                = 'paid',                 'Paid'
        PARTIALLY_PAID      = 'partially_paid',        'Partially Paid'
        VOID                = 'void',                  'Void'
        REFUNDED            = 'refunded',              'Refunded'
        PARTIALLY_REFUNDED  = 'partially_refunded',    'Partially Refunded'
        CANCELLED           = 'cancelled',             'Cancelled'

    gym    = models.ForeignKey('Gym.Gym', on_delete=models.CASCADE,
                                related_name='invoices', db_index=True)
    member = models.ForeignKey('AuthFit.Enrollment', on_delete=models.SET_NULL,
                                null=True, blank=True, related_name='invoices')
    REVENUE_STATUSES = [Status.ISSUED, Status.PAID, Status.PARTIALLY_PAID, Status.PARTIALLY_REFUNDED]
    # Numbering
    invoice_number = models.CharField(max_length=30, db_index=True)
    financial_year = models.CharField(max_length=7)   # "2026-27"
    invoice_date   = models.DateField()

    invoice_type = models.CharField(max_length=20, choices=InvoiceType.choices,
                                    default=InvoiceType.TAX_INVOICE)
    status = models.CharField(max_length=20, choices=Status.choices,
                               default=Status.ISSUED)
    cancellation_reason = models.TextField(blank=True)

    # ── Customer snapshot (frozen at invoice time) ────────────────────────
    # Never FK-dereference live — invoice must stay valid even if member edits profile
    customer_name        = models.CharField(max_length=255)
    customer_phone       = models.CharField(max_length=10, blank=True)
    customer_address     = models.TextField(blank=True)
    customer_gstin       = models.CharField(max_length=15, blank=True)
    customer_state       = models.CharField(max_length=100)
    customer_state_code  = models.CharField(max_length=2)

    place_of_supply_state = models.CharField(max_length=100)
    place_of_supply_code  = models.CharField(max_length=2)

    # ── Tax totals ────────────────────────────────────────────────────────
    taxable_value = models.DecimalField(max_digits=10, decimal_places=2)
    cgst_amount   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sgst_amount   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    igst_amount   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    round_off     = models.DecimalField(max_digits=5,  decimal_places=2, default=0)
    grand_total   = models.DecimalField(max_digits=10, decimal_places=2)

    # ── Source links ──────────────────────────────────────────────────────
    related_payment = models.OneToOneField(
        'billing.Payment', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='invoice'
    )
    related_order = models.ForeignKey(
        'Shop.Order', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='invoices'
    )

    pdf_url    = models.URLField(blank=True)   # Cloudinary URL once generated
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['gym', 'invoice_number', 'financial_year'],
                name='unique_invoice_number_per_gym_per_fy'
            )
        ]
        indexes = [
            models.Index(fields=['gym', 'financial_year', 'invoice_number']),
            models.Index(fields=['gym', 'invoice_date']),
        ]
        ordering            = ['-invoice_date', '-created_at']
        verbose_name        = 'Invoice'
        verbose_name_plural = 'Invoices'

    def __str__(self):
        return f"{self.invoice_number} — {self.customer_name} (₹{self.grand_total})"

    @property
    def is_tax_invoice(self):
        return self.invoice_type == self.InvoiceType.TAX_INVOICE

    @property
    def is_cancelled(self):
        return self.status in (self.Status.CANCELLED, self.Status.VOID)
    @property
    def refunded_amount(self):
        """Sum of all COMPLETED refunds against this invoice — gross (GST-inclusive)."""
        from django.db.models import Sum
        result = self.refunds.filter(status=Refund.Status.COMPLETED).aggregate(total=Sum('amount'))
        return result['total'] or Decimal('0')

    @property
    def refundable_amount(self):
        """How much of grand_total is still available to refund."""
        return self.grand_total - self.refunded_amount

    @property
    def is_fully_refunded(self):
        return self.refundable_amount <= 0
    @property
    def counts_toward_revenue(self) -> bool:
        return self.status in self.REVENUE_STATUSES
    @property
    def revenue_amount(self):
        return self.taxable_value
    @property
    def collection_amount(self):
        return self.grand_total


# ──────────────────────────────────────────────────────────────────────────────
# Invoice line items
# ──────────────────────────────────────────────────────────────────────────────
class InvoiceLineItem(models.Model):
    invoice      = models.ForeignKey(Invoice, on_delete=models.CASCADE,
                                     related_name='line_items')
    description  = models.CharField(max_length=255)   # e.g. "Gold Membership – 3 months"
    hsn_sac_code = models.CharField(max_length=8)
    quantity     = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price   = models.DecimalField(max_digits=10, decimal_places=2)
    taxable_value = models.DecimalField(max_digits=10, decimal_places=2)
    gst_rate     = models.DecimalField(max_digits=5, decimal_places=2,
                                       help_text="e.g. 18.00 for 18%")
    cgst_amount  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sgst_amount  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    igst_amount  = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name        = 'Invoice Line Item'
        verbose_name_plural = 'Invoice Line Items'

    def __str__(self):
        return f"{self.invoice.invoice_number} — {self.description}"

    @property
    def line_total(self):
        return self.taxable_value + self.cgst_amount + self.sgst_amount + self.igst_amount
class Refund(models.Model):
    """
    Adjustment ledger. Per spec: refunds must never delete or modify the
    original Payment or Invoice — this is a pure append-only record that
    reduces reported revenue/collection without touching history.

    amount is GROSS (GST-inclusive) — the actual sum handed back to the
    member, matching how Payment.paid_amount is also gross. RevenueService
    derives the GST-exclusive revenue-share of a refund from the parent
    invoice's own tax ratio at query time — see revenue_service.py.
    """
    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending'
        COMPLETED = 'completed', 'Completed'
        FAILED    = 'failed',    'Failed'

    class Method(models.TextChoices):
        CASH     = 'cash',     'Cash'
        UPI      = 'upi',      'UPI'
        BANK     = 'bank',     'Bank Transfer'
        ORIGINAL = 'original', 'Original Payment Method'

    gym     = models.ForeignKey('Gym.Gym', on_delete=models.CASCADE,
                                 related_name='refunds', db_index=True)
    invoice = models.ForeignKey('billing.Invoice', on_delete=models.PROTECT,
                                 related_name='refunds')

    amount = models.DecimalField(max_digits=10, decimal_places=2,
                                  help_text="Gross amount refunded (GST-inclusive).")
    reason = models.TextField(blank=True)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.ORIGINAL)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COMPLETED)

    refund_date = models.DateField(
        default=timezone.localdate,
        help_text="Date the refund was actually issued — used for revenue "
                   "reporting attribution, deliberately independent of the "
                   "original invoice_date.",
    )

    refunded_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='refunds_issued',
    )
    failure_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-refund_date', '-created_at']
        indexes = [
            models.Index(fields=['gym', 'refund_date']),
            models.Index(fields=['invoice']),
            models.Index(fields=['gym', 'status']),
        ]
        verbose_name = 'Refund'
        verbose_name_plural = 'Refunds'

    def __str__(self):
        return f"Refund ₹{self.amount} — {self.invoice.invoice_number} ({self.get_status_display()})"

    @property
    def revenue_share(self):
        """
        GST-exclusive portion of this refund, derived from the parent
        invoice's own taxable_value / grand_total ratio. Assumes a single
        blended tax rate per invoice, which matches how invoices are
        currently generated (one line item per membership payment).
        """
        from decimal import Decimal, ROUND_HALF_UP

        invoice = self.invoice
        if not invoice.grand_total:
            return self.amount  # defensive — avoid div-by-zero on a malformed invoice

        ratio = invoice.taxable_value / invoice.grand_total
        return (self.amount * ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

@receiver([post_save, post_delete], sender=Invoice)
def clear_revenue_cache_on_invoice_change(sender, instance, **kwargs):
    """
    Fires on every invoice create/update/delete — including status
    transitions to VOID/REFUNDED/CANCELLED, which is exactly when a
    previously-counted revenue figure needs to disappear immediately,
    not wait for the 60s cache TTL.
    """
    cache.delete(f"admin_revenue_{instance.gym_id}")
    cache.delete(f"admin_attendance_data_{instance.gym_id}")  # dashboard shares some context — harmless extra clear


@receiver([post_save, post_delete], sender=Payment)
def clear_revenue_cache_on_payment_change(sender, instance, **kwargs):
    cache.delete(f"admin_revenue_{instance.gym_id}")

@receiver([post_save, post_delete], sender=Refund)
def clear_revenue_cache_on_refund_change(sender, instance, **kwargs):
    """A refund changes net revenue for its gym immediately — same
    invalidation rule as Invoice/Payment writes."""
    cache.delete(f"admin_revenue_{instance.gym_id}")
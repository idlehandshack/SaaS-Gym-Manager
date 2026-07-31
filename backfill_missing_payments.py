from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone

from AuthFit.models import Enrollment
from billing.models import Payment
from billing.services.invoice_generator import create_invoice_for_payment
from billing.services.pdf_generator import generate_invoice_pdf

# Optional: scope to a single gym. Leave as None to check all gyms.
GYM = None   # e.g. Gym.objects.get(gym_code="golden-gym")

qs = Enrollment.objects.filter(paidAmount__gt=0, is_deleted=False)
if GYM:
    qs = qs.filter(gym=GYM)

created, skipped, errors = [], [], []

for enrollment in qs.select_related('gym', 'selectPlan'):
    existing_total = (
        Payment.objects.filter(enrollment=enrollment)
        .aggregate(total=Sum('paid_amount'))['total'] or 0
    )
    uninvoiced_amount = Decimal(str(enrollment.paidAmount)) - Decimal(str(existing_total))

    if uninvoiced_amount <= 0:
        skipped.append(enrollment.unique_id)
        continue

    try:
        payment = Payment.objects.create(
            gym=enrollment.gym,
            enrollment=enrollment,
            member_name=enrollment.fullname,
            member_phone=enrollment.phone,
            member_unique_id=enrollment.unique_id,
            plan_name=enrollment.selectPlan.plan if enrollment.selectPlan else '',
            plan_duration_days=enrollment.selectPlan.duration_days if enrollment.selectPlan else 30,
            amount=float(enrollment.selectPlan.price) if enrollment.selectPlan else float(enrollment.Amount),
            paid_amount=uninvoiced_amount,
            pending_amount=enrollment.pendingAmount,
            payment_method=enrollment.paymentMethod or None,
            payment_date=enrollment.paymentDate or enrollment.doj or timezone.localdate(),
            membership_start=enrollment.doj,
            membership_end=enrollment.DueDate,
        )
        invoice = create_invoice_for_payment(payment)
        try:
            generate_invoice_pdf(invoice)
        except Exception as pdf_err:
            print(f"  ⚠ PDF failed for {enrollment.unique_id}: {pdf_err}")

        enrollment.initial_invoice_generated = True
        enrollment.save(update_fields=["initial_invoice_generated"])

        created.append((enrollment.unique_id, invoice.invoice_number, str(uninvoiced_amount)))

    except Exception as e:
        errors.append((enrollment.unique_id, str(e)))

print(f"\n✅ Created {len(created)} invoices:")
for uid, inv_num, amt in created:
    print(f"   {uid} → {inv_num} (₹{amt})")

print(f"\n⏭  Skipped {len(skipped)} (already fully invoiced): {skipped}")

if errors:
    print(f"\n❌ Errors ({len(errors)}):")
    for uid, err in errors:
        print(f"   {uid}: {err}")
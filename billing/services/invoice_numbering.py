"""
billing/services/invoice_numbering.py
---------------------------------------
Thread-safe sequential invoice numbering per gym per Indian financial year.
Uses SELECT FOR UPDATE so concurrent requests never produce duplicate numbers.

InvoiceCounter model lives in billing/models.py (NOT here) so Django
auto-discovers it during migrations.
"""
from datetime import date

from django.db import transaction

from billing.models import InvoiceCounter


def get_current_financial_year(as_of: date = None) -> str:
    """
    Returns the Indian financial year string for a given date.
    Indian FY runs April 1 → March 31.

    Examples:
        2026-03-15  →  "2025-26"   (still in FY that started Apr 2025)
        2026-04-01  →  "2026-27"   (new FY starts)
    """
    as_of = as_of or date.today()
    if as_of.month >= 4:
        start = as_of.year
    else:
        start = as_of.year - 1
    return f"{start}-{str(start + 1)[-2:]}"   # "2026-27"


@transaction.atomic
def generate_invoice_number(gym, invoice_date: date) -> tuple[str, str]:
    """
    Atomically increments the gym's counter for the financial year and
    returns (invoice_number, financial_year).

    invoice_number format: <PREFIX>/<FY>/<SEQUENCE>
    e.g.  INV/2026-27/0001

    The prefix comes from gym.gst_profile.invoice_series_prefix.
    Falls back to "INV" if the gym has no GST profile yet.
    """
    fy = get_current_financial_year(invoice_date)

    # Lock the counter row for this gym+FY so no two requests get the same number
    counter, _ = InvoiceCounter.objects.select_for_update().get_or_create(
        gym=gym,
        financial_year=fy,
        defaults={'last_number': 0},
    )
    counter.last_number += 1
    counter.save(update_fields=['last_number'])

    try:
        prefix = gym.gst_profile.invoice_series_prefix or 'INV'
    except Exception:
        prefix = 'INV'

    invoice_number = f"{prefix}/{fy}/{counter.last_number:04d}"
    return invoice_number, fy

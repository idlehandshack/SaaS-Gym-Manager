"""
billing/services/tax_calculator.py
------------------------------------
Pure function — no Django imports needed. Calculates CGST/SGST vs IGST split
based on whether the gym and customer are in the same state (intrastate vs interstate).
"""
from decimal import Decimal, ROUND_HALF_UP


def calculate_line_item_tax(
    taxable_value: Decimal,
    gst_rate: Decimal,
    gym_state_code: str,
    customer_state_code: str,
) -> tuple[Decimal, Decimal, Decimal]:
    """
    Returns (cgst, sgst, igst) for a single line item.

    Same state  → split equally into CGST + SGST (IGST = 0)
    Diff state  → full amount goes to IGST (CGST = SGST = 0)

    Handles the odd-rupee CGST/SGST split: one half gets the extra paisa if
    the tax amount is not evenly divisible by 2.
    """
    TWO_PLACES = Decimal('0.01')

    if gst_rate == 0:
        return Decimal('0.00'), Decimal('0.00'), Decimal('0.00')

    total_tax = (taxable_value * gst_rate / 100).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    if gym_state_code.strip() == customer_state_code.strip():
        # Intrastate — CGST + SGST, split equally
        half = (total_tax / 2).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        # Ensure cgst + sgst exactly equals total_tax (avoids ±₹0.01 drift)
        cgst = half
        sgst = total_tax - half
        return cgst, sgst, Decimal('0.00')
    else:
        # Interstate — IGST only
        return Decimal('0.00'), Decimal('0.00'), total_tax


def round_off_amount(grand_total: Decimal) -> tuple[Decimal, Decimal]:
    """
    Returns (rounded_total, round_off_adjustment).
    Indian GST invoices round grand total to nearest rupee.
    round_off can be positive (rounded up) or negative (rounded down).
    """
    TWO_PLACES = Decimal('0.01')
    rounded = grand_total.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    adjustment = (rounded - grand_total).quantize(TWO_PLACES)
    return Decimal(rounded), adjustment


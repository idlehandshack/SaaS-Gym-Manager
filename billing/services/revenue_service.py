"""
billing/services/revenue_service.py
-------------------------------------
Single source of truth for every revenue/collection number shown anywhere
in EnterGYM — dashboard cards, revenue charts, GST exports, analytics.

Rule: nothing outside this file should write `Invoice.objects.filter(status=...)`
for a revenue calculation. If a new report needs a number, add a method
here and call it — don't hand-roll another aggregate query.

Two metrics, never confused:
  - revenue    -> SUM(taxable_value) - SUM(refund revenue-share)  — GST-exclusive
  - collection -> SUM(grand_total)   - SUM(refund gross amount)   — GST-inclusive

Refunds are attributed to the period matching their OWN refund_date, not
the original invoice's invoice_date — a refund issued in a later period
reduces THAT period's figures, never rewrites an already-closed period.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum, Count, F, QuerySet, DecimalField, ExpressionWrapper
from django.db.models.functions import TruncDay, TruncMonth
from django.utils import timezone

from billing.models import Invoice, Refund


def _revenue_qs(gym=None) -> QuerySet:
    """Base queryset: only invoices that count toward revenue, per Invoice.REVENUE_STATUSES."""
    qs = Invoice.objects.filter(status__in=Invoice.REVENUE_STATUSES)
    if gym is not None:
        qs = qs.filter(gym=gym)
    return qs


def _refund_qs(gym=None) -> QuerySet:
    """Base queryset for COMPLETED refunds only — pending/failed refunds never affect figures."""
    qs = Refund.objects.filter(status=Refund.Status.COMPLETED)
    if gym is not None:
        qs = qs.filter(gym=gym)
    return qs


def _zero_if_none(value):
    return value if value is not None else Decimal('0')


def _refund_totals_for_range(gym, start_date: date, end_date: date) -> dict:
    """
    Returns {'gross': Decimal, 'revenue_share': Decimal} for COMPLETED
    refunds whose refund_date falls in [start_date, end_date].
    revenue_share is computed per-refund via each invoice's own tax
    ratio, then summed in Python — small result sets (refunds are rare
    relative to invoices), so this avoids a fragile SQL-level ratio expression.
    """
    refunds = (
        _refund_qs(gym)
        .filter(refund_date__gte=start_date, refund_date__lte=end_date)
        .select_related('invoice')
    )
    gross = Decimal('0')
    revenue_share = Decimal('0')
    for r in refunds:
        gross += r.amount
        revenue_share += r.revenue_share
    return {"gross": gross, "revenue_share": revenue_share}


# ── Point-in-time figures ──────────────────────────────────────────────────

def get_revenue_for_range(gym, start_date: date, end_date: date) -> Decimal:
    """GST-exclusive revenue for [start_date, end_date], net of refunds issued in the same window."""
    result = (
        _revenue_qs(gym)
        .filter(invoice_date__gte=start_date, invoice_date__lte=end_date)
        .aggregate(total=Sum('taxable_value'))
    )
    gross_revenue = _zero_if_none(result['total'])
    refund_totals = _refund_totals_for_range(gym, start_date, end_date)
    return gross_revenue - refund_totals['revenue_share']


def get_collection_for_range(gym, start_date: date, end_date: date) -> Decimal:
    """GST-inclusive collection for [start_date, end_date], net of refunds issued in the same window."""
    result = (
        _revenue_qs(gym)
        .filter(invoice_date__gte=start_date, invoice_date__lte=end_date)
        .aggregate(total=Sum('grand_total'))
    )
    gross_collection = _zero_if_none(result['total'])
    refund_totals = _refund_totals_for_range(gym, start_date, end_date)
    return gross_collection - refund_totals['gross']


def get_today_figures(gym) -> dict:
    today = timezone.localdate()
    return {
        "revenue":    get_revenue_for_range(gym, today, today),
        "collection": get_collection_for_range(gym, today, today),
    }


def get_month_figures(gym, today=None) -> dict:
    today = today or timezone.localdate()
    month_start = today.replace(day=1)
    return {
        "revenue":    get_revenue_for_range(gym, month_start, today),
        "collection": get_collection_for_range(gym, month_start, today),
    }


def get_lifetime_figures(gym) -> dict:
    result = _revenue_qs(gym).aggregate(
        revenue=Sum('taxable_value'),
        collection=Sum('grand_total'),
    )
    refund_result = _refund_qs(gym).aggregate(gross=Sum('amount'))
    gross_refund_total = _zero_if_none(refund_result['gross'])

    # For lifetime revenue_share, sum per-refund shares (no date window to bound it).
    revenue_share_total = Decimal('0')
    for r in _refund_qs(gym).select_related('invoice'):
        revenue_share_total += r.revenue_share

    return {
        "revenue":    _zero_if_none(result['revenue']) - revenue_share_total,
        "collection": _zero_if_none(result['collection']) - gross_refund_total,
    }


def get_total_refunds(gym, start_date: date = None, end_date: date = None) -> Decimal:
    """Exposed separately so a dashboard can show 'Refunded Today' etc. per the analytics spec."""
    qs = _refund_qs(gym)
    if start_date and end_date:
        qs = qs.filter(refund_date__gte=start_date, refund_date__lte=end_date)
    return _zero_if_none(qs.aggregate(total=Sum('amount'))['total'])


# ── Time-series (for charts) ───────────────────────────────────────────────

def get_daily_series(gym, days: int, metric: str = 'revenue') -> list[dict]:
    """
    Returns [{"date": date, "value": Decimal}, ...] for the last `days` days,
    including today, gap-filled with 0, net of same-day refunds.
    metric: 'revenue' (taxable_value net of refund revenue-share) or
            'collection' (grand_total net of refund gross amount).
    """
    field = 'taxable_value' if metric == 'revenue' else 'grand_total'
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)

    rows = (
        _revenue_qs(gym)
        .filter(invoice_date__gte=start, invoice_date__lte=today)
        .annotate(day=TruncDay('invoice_date'))
        .values('day')
        .annotate(total=Sum(field))
        .order_by('day')
    )
    by_day = {r['day']: _zero_if_none(r['total']) for r in rows if r['day']}

    # Refunds netted in by their OWN date, same rule as the point-in-time methods.
    refund_rows = (
        _refund_qs(gym)
        .filter(refund_date__gte=start, refund_date__lte=today)
        .select_related('invoice')
    )
    refund_by_day = {}
    for r in refund_rows:
        key = r.refund_date
        share = r.revenue_share if metric == 'revenue' else r.amount
        refund_by_day[key] = refund_by_day.get(key, Decimal('0')) + share

    return [
        {
            "date": start + timedelta(days=i),
            "value": by_day.get(start + timedelta(days=i), Decimal('0'))
                      - refund_by_day.get(start + timedelta(days=i), Decimal('0')),
        }
        for i in range(days)
    ]


def get_monthly_series(gym, months: int, metric: str = 'revenue') -> list[dict]:
    """
    Returns [{"year": int, "month": int, "value": Decimal}, ...] for the
    last `months` calendar months, oldest first, gap-filled with 0, net of
    refunds issued in each respective month.
    """
    field = 'taxable_value' if metric == 'revenue' else 'grand_total'
    today = timezone.localdate()

    month_keys = []
    y, m = today.year, today.month
    for _ in range(months):
        month_keys.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    month_keys.reverse()

    start = today.replace(year=month_keys[0][0], month=month_keys[0][1], day=1)

    rows = (
        _revenue_qs(gym)
        .filter(invoice_date__gte=start, invoice_date__lte=today)
        .annotate(month=TruncMonth('invoice_date'))
        .values('month')
        .annotate(total=Sum(field))
    )
    by_month = {(r['month'].year, r['month'].month): _zero_if_none(r['total']) for r in rows if r['month']}

    refund_rows = (
        _refund_qs(gym)
        .filter(refund_date__gte=start, refund_date__lte=today)
        .select_related('invoice')
    )
    refund_by_month = {}
    for r in refund_rows:
        key = (r.refund_date.year, r.refund_date.month)
        share = r.revenue_share if metric == 'revenue' else r.amount
        refund_by_month[key] = refund_by_month.get(key, Decimal('0')) + share

    return [
        {
            "year": y, "month": m,
            "value": by_month.get((y, m), Decimal('0')) - refund_by_month.get((y, m), Decimal('0')),
        }
        for (y, m) in month_keys
    ]


# ── Plan-wise breakdown ─────────────────────────────────────────────────────

def get_plan_revenue_breakdown(gym) -> list[dict]:
    """
    Revenue grouped by plan, via the immutable plan_name snapshot on the
    related Payment. NOTE: does not currently net out refunds per-plan —
    refunds aren't linked to a plan breakdown dimension. Revisit if a
    per-plan refund view becomes a requirement.
    """
    rows = (
        _revenue_qs(gym)
        .select_related('related_payment')
        .exclude(related_payment__isnull=True)
        .values('related_payment__plan_name')
        .annotate(revenue=Sum('taxable_value'), collection=Sum('grand_total'), count=Count('id'))
        .order_by('-revenue')
    )
    return [
        {
            "plan_name": r['related_payment__plan_name'] or 'Unknown',
            "revenue":   _zero_if_none(r['revenue']),
            "collection": _zero_if_none(r['collection']),
            "count":     r['count'],
        }
        for r in rows
    ]


# ── GST-specific (used by gst_report.py) ────────────────────────────────────

def get_invoices_for_gst_report(gym, start_date: date, end_date: date):
    """Invoices eligible for GST reporting — same revenue-status gate as everywhere else."""
    return (
        _revenue_qs(gym)
        .filter(invoice_date__range=(start_date, end_date))
        .prefetch_related('line_items')
        .order_by('invoice_date', 'invoice_number')
    )

def get_week_figures(gym, today=None) -> dict:
    """Trailing 7 days including today — a real weekly figure, distinct
    from calendar-month. Used by the Revenue Dashboard's Weekly card."""
    today = today or timezone.localdate()
    week_start = today - timedelta(days=6)
    return {
        "revenue":    get_revenue_for_range(gym, week_start, today),
        "collection": get_collection_for_range(gym, week_start, today),
    }

def get_monthly_series_since(gym, start_date: date, metric: str = 'revenue') -> list[dict]:
    """
    Same shape as get_monthly_series(), but spans from `start_date` (typically
    the gym's own join/created date) through the current month — instead of
    a fixed lookback window. A gym that joined 3 months ago gets a 3-month
    trend, not 18 months padded with empty zeros.
    """
    field = 'taxable_value' if metric == 'revenue' else 'grand_total'
    today = timezone.localdate()

    month_keys = []
    y, m = start_date.year, start_date.month
    while (y, m) <= (today.year, today.month):
        month_keys.append((y, m))
        m += 1
        if m == 13:
            m = 1
            y += 1

    start = start_date.replace(day=1)

    rows = (
        _revenue_qs(gym)
        .filter(invoice_date__gte=start, invoice_date__lte=today)
        .annotate(month=TruncMonth('invoice_date'))
        .values('month')
        .annotate(total=Sum(field))
    )
    by_month = {(r['month'].year, r['month'].month): _zero_if_none(r['total']) for r in rows if r['month']}

    refund_rows = (
        _refund_qs(gym)
        .filter(refund_date__gte=start, refund_date__lte=today)
        .select_related('invoice')
    )
    refund_by_month = {}
    for r in refund_rows:
        key = (r.refund_date.year, r.refund_date.month)
        share = r.revenue_share if metric == 'revenue' else r.amount
        refund_by_month[key] = refund_by_month.get(key, Decimal('0')) + share

    return [
        {
            "year": y, "month": m,
            "value": by_month.get((y, m), Decimal('0')) - refund_by_month.get((y, m), Decimal('0')),
        }
        for (y, m) in month_keys
    ]

def is_gst_enabled(gym) -> bool:
    """
    True only when the gym has a GST profile, is registered, and isn't
    on composition scheme — the exact same condition invoice_generator.py
    uses to decide TAX_INVOICE vs BILL_OF_SUPPLY. Centralized here so
    dashboard/revenue views never duplicate this check.
    """
    if gym is None:
        return False
    gst_profile = getattr(gym, 'gst_profile', None)
    if gst_profile is None:
        return False
    return bool(gst_profile.is_gst_registered and not gst_profile.composition_scheme)
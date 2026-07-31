"""
expenses/services.py
--------------------
All business logic for the Expense module lives here. Views should stay thin
and only call into these functions.

IMPORTANT: this module never writes to billing/AuthFit models. Revenue is
read read-only from AuthFit.models.Enrollment (paidAmount), filtered on
is_deleted=False — the exact same source and filter used by the main
Dashboard Overview (Gym/dashboard_views.py).

We deliberately do NOT use billing.models.Payment for revenue. Payment.
enrollment is a SET_NULL foreign key, and enrollment soft-deletion (e.g. an
owner deleting a duplicate enrollment via Freeze Membership > Delete
Enrollment) does not touch the Payment row at all — the payment snapshot
stays in the table and would permanently inflate revenue for a member that
no longer counts anywhere else. Reading from Enrollment.paidAmount with
is_deleted=False sidesteps that entirely and keeps this dashboard's revenue
figure identical to the main dashboard's, by construction.

RECURRING EXPENSES
-------------------
- ExpenseTemplate.next_run_date holds the next due date for that template.
- generate_recurring_expenses() only looks at templates where
  next_run_date <= today — a cheap indexed filter instead of scanning every
  active template every run.
- For each due template, generate every month from next_run_date up to the
  current month (loop handles catch-up if a run was missed), then advance
  next_run_date to the month after the last one generated.
- The amount used for each new occurrence is the latest existing occurrence's
  amount if one exists, else the template's starting amount — so an owner
  editing one month's amount (e.g. rent increase) carries forward automatically.
"""
from datetime import date, timedelta

from django.db.models import Sum, Q
from django.utils import timezone

from billing.services import revenue_service
from .models import Expense, ExpenseTemplate


# ──────────────────────────────────────────────────────────────────────────
# CRUD — Expense
# ──────────────────────────────────────────────────────────────────────────

def create_expense(gym, user, *, title, category, amount, payment_method,
                    expense_date=None, note='', receipt=None, is_recurring=False):
    if not title:
        title = Expense.CATEGORY_DEFAULT_TITLES.get(category, 'Expense')

    expense_date = expense_date or timezone.localdate()
    template = None

    if is_recurring:
        # Quick-add checkbox path: create (or reuse) a template so this
        # expense starts a real recurring schedule, not just a one-off flag.
        template = get_or_create_template_for_quick_add(
            gym, user, title=title, category=category, amount=amount,
            payment_method=payment_method, start_date=expense_date, note=note,
        )

    return Expense.objects.create(
        gym=gym,
        title=title,
        category=category,
        amount=amount,
        payment_method=payment_method,
        expense_date=expense_date,
        note=note,
        receipt=receipt,
        is_recurring=is_recurring,
        template=template,
        created_by=user,
    )


def update_expense(expense, **fields):
    for key, value in fields.items():
        if value is not None:
            setattr(expense, key, value)
    expense.save()
    return expense


def delete_expense(expense):
    expense.delete()


# ──────────────────────────────────────────────────────────────────────────
# CRUD — ExpenseTemplate
# ──────────────────────────────────────────────────────────────────────────

def get_or_create_template_for_quick_add(gym, user, *, title, category, amount,
                                          payment_method, start_date, note=''):
    """Used by the quick-add 'Recurring monthly' checkbox. Reuses an existing
    active template with the same gym/title/category if one exists, so
    re-checking the box on a familiar expense doesn't spawn duplicates."""
    template = ExpenseTemplate.objects.filter(
        gym=gym, title=title, category=category, is_active=True,
    ).first()
    if template:
        return template
    return create_template(
        gym, user, title=title, category=category, amount=amount,
        payment_method=payment_method, note=note, start_date=start_date,
    )


def create_template(gym, user, *, title, category, amount, payment_method,
                     note='', start_date=None, end_date=None):
    start_date = start_date or timezone.localdate()
    return ExpenseTemplate.objects.create(
        gym=gym, title=title, category=category, amount=amount,
        payment_method=payment_method, note=note,
        start_date=start_date,
        next_run_date=start_date.replace(day=1),
        end_date=end_date, created_by=user,
    )


def pause_template(template):
    template.is_active = False
    template.save(update_fields=['is_active'])


def resume_template(template, today=None):
    """Resuming does NOT replay months missed while paused — next_run_date
    jumps forward to the current month so generation picks up from now."""
    today = today or timezone.localdate()
    current_month_start = today.replace(day=1)
    if template.next_run_date < current_month_start:
        template.next_run_date = current_month_start
    template.is_active = True
    template.save(update_fields=['is_active', 'next_run_date'])


# ──────────────────────────────────────────────────────────────────────────
# Recurring generation
# ──────────────────────────────────────────────────────────────────────────

def _add_month(d):
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def _generate_one_occurrence(template, month_start):
    """Create the occurrence for `month_start` if one doesn't already exist
    for this template/month (safety net — next_run_date normally prevents
    re-entry, but this guards against manual edits/admin tinkering)."""
    exists = Expense.objects.filter(
        template=template,
        expense_date__year=month_start.year,
        expense_date__month=month_start.month,
    ).exists()
    if exists:
        return None

    latest = (
        Expense.objects.filter(template=template, expense_date__lt=month_start)
        .order_by('-expense_date')
        .first()
    )
    amount = latest.amount if latest else template.amount
    payment_method = latest.payment_method if latest else template.payment_method

    return Expense.objects.create(
        gym=template.gym,
        title=template.title,
        category=template.category,
        amount=amount,
        payment_method=payment_method,
        expense_date=month_start,
        note=template.note,
        is_recurring=True,
        template=template,
        created_by=None,  # system-generated
    )


def _run_template(template, up_to_month_start):
    """Generate every occurrence from template.next_run_date up to and
    including up_to_month_start, then advance next_run_date past the last
    one generated. Loop provides catch-up if a run was missed."""
    created = []
    guard = 0  # safety cap so a bad next_run_date can't spin this forever
    next_month = template.next_run_date.replace(day=1)

    while next_month <= up_to_month_start and guard < 240:
        result = _generate_one_occurrence(template, next_month)
        if result:
            created.append(result)
        next_month = _add_month(next_month)
        guard += 1

    if next_month != template.next_run_date:
        template.next_run_date = next_month
        template.save(update_fields=['next_run_date'])

    return created


def generate_recurring_expenses(gym=None, today=None):
    """
    Process only templates due for generation (next_run_date <= today) —
    a cheap indexed filter instead of scanning every active template.
    Safe to call repeatedly (cron, or opportunistically on dashboard load).

    Pass `gym` to limit to one tenant (cheap, dashboard-load usage);
    omit it to process every gym (cron usage).
    """
    today = today or timezone.localdate()
    current_month_start = today.replace(day=1)

    templates = ExpenseTemplate.objects.filter(
        is_active=True,
        next_run_date__lte=today,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    )
    if gym is not None:
        templates = templates.filter(gym=gym)

    all_created = []
    for template in templates:
        all_created.extend(_run_template(template, current_month_start))
    return all_created


# ──────────────────────────────────────────────────────────────────────────
# Aggregates
# ──────────────────────────────────────────────────────────────────────────

def _month_bounds(today=None):
    today = today or timezone.localdate()
    start = today.replace(day=1)
    end = (start.replace(year=start.year + 1, month=1) if start.month == 12
           else start.replace(month=start.month + 1))
    return start, end


def get_monthly_expense(gym, today=None):
    start, end = _month_bounds(today)
    total = (
        Expense.objects.filter(gym=gym, expense_date__gte=start, expense_date__lt=end)
        .aggregate(total=Sum('amount'))['total']
    )
    return total or 0


def get_today_expense(gym, today=None):
    today = today or timezone.localdate()
    total = (
        Expense.objects.filter(gym=gym, expense_date=today)
        .aggregate(total=Sum('amount'))['total']
    )
    return total or 0


def get_monthly_revenue(gym, today=None):
    """
    Sourced from RevenueService — GST-exclusive business revenue, net of
    refunds issued in the same period. Matches Gym/dashboard_views.py's
    Monthly Revenue card exactly, by construction, since both call the
    same underlying function.
    """
    today = today or timezone.localdate()
    figures = revenue_service.get_month_figures(gym, today)
    return figures['revenue']


def get_monthly_profit(gym, today=None):
    """Revenue - Expenses - Refunds, per spec. Refunds are already netted
    out of get_monthly_revenue() above via RevenueService — do not
    subtract them again here."""
    revenue = get_monthly_revenue(gym, today)
    expense = get_monthly_expense(gym, today)
    return revenue - expense


def get_expense_by_category(gym, today=None):
    start, end = _month_bounds(today)
    rows = (
        Expense.objects.filter(gym=gym, expense_date__gte=start, expense_date__lt=end)
        .values('category')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )
    return [
        {
            'category': r['category'],
            'label': dict(Expense.Category.choices).get(r['category'], r['category']),
            'total': float(r['total'] or 0),
        }
        for r in rows
    ]


def _month_keys(n_months, today):
    months = []
    y, m = today.year, today.month
    for _ in range(n_months):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()
    return months


def get_expense_trend(gym, n_months=12, today=None):
    """Last n_months of total expense, oldest first — gap-free (0 for empty months)."""
    today = today or timezone.localdate()
    months = _month_keys(n_months, today)
    start = today.replace(year=months[0][0], month=months[0][1], day=1)

    rows = (
        Expense.objects.filter(gym=gym, expense_date__gte=start, expense_date__lte=today)
        .values('expense_date__year', 'expense_date__month')
        .annotate(total=Sum('amount'))
    )
    row_map = {(r['expense_date__year'], r['expense_date__month']): float(r['total'] or 0) for r in rows}

    labels = [f"{today.replace(year=y, month=m, day=1):%b %Y}" for (y, m) in months]
    data = [row_map.get((y, m), 0) for (y, m) in months]
    return labels, data


def get_revenue_vs_expense_trend(gym, n_months=12, today=None):
    """
    Revenue series now sourced from RevenueService.get_monthly_series(),
    same GST-exclusive/refund-netted figures as everywhere else — was
    previously a hand-rolled Enrollment aggregate that has now been removed.
    """
    today = today or timezone.localdate()
    months = _month_keys(n_months, today)
    start = today.replace(year=months[0][0], month=months[0][1], day=1)

    exp_rows = (
        Expense.objects.filter(gym=gym, expense_date__gte=start, expense_date__lte=today)
        .values('expense_date__year', 'expense_date__month')
        .annotate(total=Sum('amount'))
    )
    exp_map = {(r['expense_date__year'], r['expense_date__month']): float(r['total'] or 0) for r in exp_rows}

    revenue_series = revenue_service.get_monthly_series(gym, months=n_months, metric='revenue')
    rev_map = {(r['year'], r['month']): float(r['value']) for r in revenue_series}

    labels = [f"{today.replace(year=y, month=m, day=1):%b %Y}" for (y, m) in months]
    revenue_data = [rev_map.get((y, m), 0) for (y, m) in months]
    expense_data = [exp_map.get((y, m), 0) for (y, m) in months]
    profit_data = [round(r - e, 2) for r, e in zip(revenue_data, expense_data)]
    return labels, revenue_data, expense_data, profit_data


def get_dashboard_summary(gym, today=None):
    today = today or timezone.localdate()
    monthly_revenue = get_monthly_revenue(gym, today)
    monthly_expense = get_monthly_expense(gym, today)
    monthly_profit = monthly_revenue - monthly_expense
    today_expense = get_today_expense(gym, today)

    category_breakdown = get_expense_by_category(gym, today)
    trend_labels, trend_data = get_expense_trend(gym, 12, today)
    rve_labels, rve_revenue, rve_expense, rve_profit = get_revenue_vs_expense_trend(gym, 12, today)

    return {
        'monthly_revenue': float(monthly_revenue),
        'monthly_expense': float(monthly_expense),
        'monthly_profit': float(monthly_profit),
        'today_expense': float(today_expense),
        'profit_positive': monthly_profit >= 0,
        'category_breakdown': category_breakdown,
        'expense_trend_labels': trend_labels,
        'expense_trend_data': trend_data,
        'revenue_vs_expense_labels': rve_labels,
        'revenue_vs_expense_revenue': rve_revenue,
        'revenue_vs_expense_expense': rve_expense,
        'revenue_vs_expense_profit': rve_profit,
    }


# ──────────────────────────────────────────────────────────────────────────
# List / filter (used by the Expense List page)
# ──────────────────────────────────────────────────────────────────────────

def get_filtered_expenses(gym, *, date_filter=None, start_date=None, end_date=None,
                           category=None, payment_method=None,
                           amount_min=None, amount_max=None, search=None):
    qs = Expense.objects.filter(gym=gym).select_related('created_by')
    today = timezone.localdate()

    if date_filter == 'today':
        qs = qs.filter(expense_date=today)
    elif date_filter == 'yesterday':
        qs = qs.filter(expense_date=today - timedelta(days=1))
    elif date_filter == 'this_week':
        start = today - timedelta(days=today.weekday())
        qs = qs.filter(expense_date__gte=start, expense_date__lte=today)
    elif date_filter == 'this_month':
        start, end = _month_bounds(today)
        qs = qs.filter(expense_date__gte=start, expense_date__lt=end)
    elif date_filter == 'last_month':
        this_start, _ = _month_bounds(today)
        last_end = this_start
        last_start = (last_end.replace(day=1) - timedelta(days=1)).replace(day=1)
        qs = qs.filter(expense_date__gte=last_start, expense_date__lt=last_end)
    elif date_filter == 'custom' and start_date and end_date:
        qs = qs.filter(expense_date__gte=start_date, expense_date__lte=end_date)

    if category:
        qs = qs.filter(category=category)
    if payment_method:
        qs = qs.filter(payment_method=payment_method)
    if amount_min is not None:
        qs = qs.filter(amount__gte=amount_min)
    if amount_max is not None:
        qs = qs.filter(amount__lte=amount_max)
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(note__icontains=search))

    return qs.order_by('-expense_date', '-created_at')
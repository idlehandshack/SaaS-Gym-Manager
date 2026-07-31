from datetime import datetime

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST, require_GET
from django.utils import timezone

from AuthFit.permissions import permission_required
from .models import Expense
from . import services


def _gym(request):
    return getattr(request, 'gym', None)


# ──────────────────────────────────────────────────────────────────────────
# 1. Expense Dashboard
# ──────────────────────────────────────────────────────────────────────────

@permission_required("can_view_revenue")
@require_GET
def expense_dashboard(request):
    gym = _gym(request)
    summary = services.get_dashboard_summary(gym)

    return render(request, 'expenses/expensedashboard.html', {
        'gym': gym,
        'active': 'expenses',
        'today': timezone.localdate(),
        **summary,
    })


# ──────────────────────────────────────────────────────────────────────────
# 2. Add Expense
# ──────────────────────────────────────────────────────────────────────────

@permission_required("can_manage_store")  # reuse closest existing billing-adjacent permission
@require_POST
def add_expense(request):
    gym = _gym(request)

    title = request.POST.get('title', '').strip()
    category = request.POST.get('category', Expense.Category.MISCELLANEOUS)
    amount = request.POST.get('amount')
    payment_method = request.POST.get('payment_method', Expense.PaymentMethod.CASH)
    expense_date_raw = request.POST.get('expense_date')
    note = request.POST.get('note', '').strip()
    is_recurring = request.POST.get('is_recurring') == 'on'
    receipt = request.FILES.get('receipt')

    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        messages.error(request, "Please enter a valid amount.")
        return redirect('expenses:dashboard')

    expense_date = None
    if expense_date_raw:
        try:
            expense_date = datetime.strptime(expense_date_raw, '%Y-%m-%d').date()
        except ValueError:
            pass

    expense = services.create_expense(
        gym, request.user,
        title=title, category=category, amount=amount,
        payment_method=payment_method, expense_date=expense_date,
        note=note, receipt=receipt, is_recurring=is_recurring,
    )

    from AuthFit.audit import log_action
    log_action(
        gym=gym,
        action='expense_added',
        staff_user=request.user,
        request=request,
        object_type='Expense',
        object_id=expense.pk,
        object_label=expense.title,
        new_values={'amount': str(expense.amount), 'category': expense.category},
    )

    messages.success(request, "Expense recorded.")
    return redirect('expenses:list')


# ──────────────────────────────────────────────────────────────────────────
# 3. Expense List (search / filter / edit / delete)
# ──────────────────────────────────────────────────────────────────────────

@permission_required("can_view_revenue")
@require_GET
def expense_list(request):
    gym = _gym(request)

    date_filter = request.GET.get('date_filter', 'this_month')
    category = request.GET.get('category') or None
    payment_method = request.GET.get('payment_method') or None
    search = request.GET.get('q', '').strip() or None
    start_date = request.GET.get('start_date') or None
    end_date = request.GET.get('end_date') or None
    amount_min = request.GET.get('amount_min') or None
    amount_max = request.GET.get('amount_max') or None

    qs = services.get_filtered_expenses(
        gym,
        date_filter=date_filter, start_date=start_date, end_date=end_date,
        category=category, payment_method=payment_method,
        amount_min=amount_min, amount_max=amount_max, search=search,
    )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'expenses/expense_list.html', {
        'gym': gym,
        'active': 'expenses',
        'page_obj': page_obj,
        'categories': Expense.Category.choices,
        'payment_methods': Expense.PaymentMethod.choices,
        'filters': {
            'date_filter': date_filter, 'category': category,
            'payment_method': payment_method, 'q': search or '',
            'start_date': start_date or '', 'end_date': end_date or '',
            'amount_min': amount_min or '', 'amount_max': amount_max or '',
        },
        'total_count': qs.count(),
    })


@permission_required("can_manage_store")
@require_POST
def edit_expense(request, pk):
    gym = _gym(request)
    expense = get_object_or_404(Expense, pk=pk, gym=gym)

    old_values = {'title': expense.title, 'amount': str(expense.amount), 'category': expense.category}

    title = request.POST.get('title', '').strip()
    category = request.POST.get('category')
    amount = request.POST.get('amount')
    payment_method = request.POST.get('payment_method')
    note = request.POST.get('note', '').strip()

    fields = {'title': title, 'category': category, 'payment_method': payment_method, 'note': note}
    if amount:
        try:
            fields['amount'] = float(amount)
        except ValueError:
            messages.error(request, "Invalid amount.")
            return redirect('expenses:list')

    services.update_expense(expense, **fields)

    from AuthFit.audit import log_action
    log_action(
        gym=gym,
        action='expense_edited',
        staff_user=request.user,
        request=request,
        object_type='Expense',
        object_id=expense.pk,
        object_label=expense.title,
        old_values=old_values,
        new_values={'title': expense.title, 'amount': str(expense.amount), 'category': expense.category},
    )

    messages.success(request, "Expense updated.")
    return redirect('expenses:list')


@permission_required("can_manage_store")
@require_POST
def delete_expense(request, pk):
    gym = _gym(request)
    expense = get_object_or_404(Expense, pk=pk, gym=gym)

    from AuthFit.audit import log_action
    log_action(
        gym=gym,
        action='expense_deleted',
        staff_user=request.user,
        request=request,
        object_type='Expense',
        object_id=expense.pk,
        object_label=expense.title,
        old_values={'amount': str(expense.amount), 'category': expense.category},
    )

    services.delete_expense(expense)
    messages.success(request, "Expense deleted.")
    return redirect('expenses:list')
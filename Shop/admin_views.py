# Shop/admin_views.py
"""
Admin-only review pages for the global product catalog:
Pending / Approved / Rejected lists, and duplicate-merge tool.

Gated on request.user.is_superuser — this is platform-level catalog
governance, not gym-scoped.
"""

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from .models import GlobalProduct
from .services import ProductApprovalService, DuplicateMergeService, ProductSearchService
from .forms import GlobalProductEditForm, RejectProductForm, MergeProductForm


def _superuser_required(view_fn):
    return login_required(user_passes_test(lambda u: u.is_superuser)(view_fn))


@_superuser_required
def pending_products(request):
    products = ProductApprovalService.pending_queryset()
    page_obj = Paginator(products, 25).get_page(request.GET.get('page'))
    return render(request, 'shop/admin/pending_products.html', {'page_obj': page_obj, 'products': page_obj.object_list})


@_superuser_required
def approved_products(request):
    query = request.GET.get('q', '').strip()
    if query:
        products = ProductSearchService.search_global_catalog(query, approved_only=False).filter(
            approval_status=GlobalProduct.Approval.APPROVED
        )
    else:
        products = ProductApprovalService.approved_queryset()
    page_obj = Paginator(products, 25).get_page(request.GET.get('page'))
    return render(request, 'shop/admin/approved_products.html', {
        'page_obj': page_obj, 'products': page_obj.object_list, 'query': query,
    })


@_superuser_required
def rejected_products(request):
    products = ProductApprovalService.rejected_queryset()
    page_obj = Paginator(products, 25).get_page(request.GET.get('page'))
    return render(request, 'shop/admin/rejected_products.html', {'page_obj': page_obj, 'products': page_obj.object_list})


@_superuser_required
@require_POST
def approve_product(request, product_id):
    product = get_object_or_404(GlobalProduct, id=product_id)
    ProductApprovalService.approve(product, approved_by=request.user)
    messages.success(request, f'"{product.name}" approved and now visible to all gyms.')
    return redirect('admin_pending_products')


@_superuser_required
def reject_product(request, product_id):
    product = get_object_or_404(GlobalProduct, id=product_id)

    if request.method == 'POST':
        form = RejectProductForm(request.POST)
        if form.is_valid():
            ProductApprovalService.reject(
                product, rejected_by=request.user, reason=form.cleaned_data['reason']
            )
            messages.success(request, f'"{product.name}" rejected.')
            return redirect('admin_pending_products')
    else:
        form = RejectProductForm()

    return render(request, 'shop/admin/reject_product.html', {
        'product': product, 'form': form,
    })


@_superuser_required
def edit_product(request, product_id):
    product = get_object_or_404(GlobalProduct, id=product_id)

    if request.method == 'POST':
        form = GlobalProductEditForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, f'"{product.name}" updated.')
            return redirect('admin_approved_products')
    else:
        form = GlobalProductEditForm(instance=product)

    return render(request, 'shop/admin/edit_product.html', {
        'product': product, 'form': form,
    })


@_superuser_required
def merge_products(request):
    """
    Search box picks two GlobalProducts (winner/loser); on submit, merges
    the loser into the winner via DuplicateMergeService.
    """
    query = request.GET.get('q', '').strip()
    candidates = ProductSearchService.search_global_catalog(query, approved_only=False) if query else []

    if request.method == 'POST':
        form = MergeProductForm(request.POST)
        if form.is_valid():
            winner = get_object_or_404(GlobalProduct, id=form.cleaned_data['winner_id'])
            loser  = get_object_or_404(GlobalProduct, id=form.cleaned_data['loser_id'])
            try:
                DuplicateMergeService.merge(winner=winner, loser=loser, merged_by=request.user)
                messages.success(
                    request,
                    f'Merged "{loser.name}" into "{winner.name}". All gym data and order '
                    f'history preserved and repointed to "{winner.name}".'
                )
            except ValidationError as e:
                messages.error(request, str(e))
            return redirect('admin_merge_products')
        messages.error(request, "Select two different products to merge.")

    return render(request, 'shop/admin/merge_products.html', {
        'query': query, 'candidates': candidates,
    })


@_superuser_required
def admin_catalog_home(request):
    """
    /shop/admin-catalog/ — quick overview of the entire approved catalog,
    across all gyms. Separate from admin_approved_products (which is the
    paginated management list) — this is a simpler full-listing view.
    """
    query = request.GET.get('q', '').strip()

    products = GlobalProduct.objects.filter(
        approval_status=GlobalProduct.Approval.APPROVED,
    ).select_related('created_by', 'approved_by').prefetch_related('flavors').order_by('name')

    if query:
        from .services import ProductSearchService
        products = ProductSearchService.search_global_catalog(query, approved_only=True)

    from django.core.paginator import Paginator
    page_obj = Paginator(products, 30).get_page(request.GET.get('page'))

    return render(request, 'shop/admin/catalog_home.html', {
        'page_obj': page_obj,
        'products': page_obj.object_list,
        'query': query,
    })
# Shop/catalog_views.py
"""
Gym-owner facing catalog views: browse global catalog, import a product
into the gym's store, propose a new product, and manage already-imported
GymProducts (pricing, stock, visibility).
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from Gym.decorators import store_enabled_required
from Gym.mixins import gym_staff_required
from django.core.paginator import Paginator
from .models import GlobalProduct, GymProduct, GymProductFlavor
from .services import (
    ProductImportService,
    ProductCreationService,
    ProductSearchService,
    DuplicateDetectionService,
    DuplicateProductError,
    FlavorInput,
)
from django.core.exceptions import ValidationError as DjangoValidationError
from .models import GymProductFlavor
from .services import StockMovementService
from .forms import StockAdjustmentForm
from .forms import (
    NewProductForm, FlavorFormSetHelper,
    GymProductEditForm, GymProductFlavorEditForm,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Browse global catalog + import
# ──────────────────────────────────────────────────────────────────────────────

@gym_staff_required
@store_enabled_required
def catalog_browse(request):
    """Browse the approved global catalog to find products to import."""
    gym   = getattr(request, 'gym', None)
    query = request.GET.get('q', '').strip()

    results = ProductSearchService.search_global_catalog(query, approved_only=True)

    paginator = Paginator(results, 24)
    page_obj = paginator.get_page(request.GET.get('page'))

    imported_ids = set(
        GymProduct.objects.filter(gym=gym, active=True).values_list('global_product_id', flat=True)
    )

    return render(request, 'shop/catalog_browse.html', {
        'gym':           gym,
        'query':         query,
        'page_obj':      page_obj,
        'results':       page_obj.object_list,
        'imported_ids':  imported_ids,
    })


@gym_staff_required
@store_enabled_required
@require_POST
def catalog_import(request, global_product_id):
    """Gym owner clicks 'Import to My Store'."""
    gym = getattr(request, 'gym', None)
    global_product = get_object_or_404(
        GlobalProduct, id=global_product_id, approval_status=GlobalProduct.Approval.APPROVED,
    )

    try:
        gym_product = ProductImportService.import_product(gym, global_product)
    except ValidationError as e:
        messages.error(request, str(e))
        return redirect('catalog_browse')

    messages.success(
        request,
        f'"{global_product.name}" imported. Set your prices and stock to make it visible to members.'
    )
    return redirect('gym_product_edit', gym_product_id=gym_product.id)


# ──────────────────────────────────────────────────────────────────────────────
# Propose a brand-new product (goes to admin PENDING queue)
# ──────────────────────────────────────────────────────────────────────────────

@gym_staff_required
@store_enabled_required
def create_new_product(request):
    gym = getattr(request, 'gym', None)

    if request.method != 'POST':
        return render(request, 'shop/create_new_product.html', {'gym': gym})

    form = NewProductForm(request.POST, request.FILES)
    flavors_raw = FlavorFormSetHelper.parse(request)
    force_create = request.POST.get('force_create') == '1'

    flavor_pairs = list(zip(
        request.POST.getlist('flavor_name[]'),
        request.POST.getlist('weight[]'),
    ))

    def _rerender(**extra):
        context = {
            'gym': gym,
            'form': form,
            'posted': request.POST,
            'flavor_pairs': flavor_pairs,
        }
        context.update(extra)
        return render(request, 'shop/create_new_product.html', context)

    if not form.is_valid():
        messages.error(request, "Please correct the errors below.")
        return _rerender()

    if not flavors_raw:
        messages.error(request, "Add at least one flavor/variant.")
        return _rerender()

    try:
        product, gym_product = ProductCreationService.create_pending_product(
            created_by=request.user,
            gym=gym,
            brand=form.cleaned_data['brand'],
            category=form.cleaned_data['category'],
            name=form.cleaned_data['name'],
            description=form.cleaned_data['description'],
            image=form.cleaned_data.get('image'),
            flavors=[FlavorInput(**f) for f in flavors_raw],
            skip_duplicate_check=force_create,
        )
    except DuplicateProductError as e:
        return _rerender(
            duplicate_candidates=e.candidates,
            show_force_create=True,
        )
    except ValidationError as e:
        messages.error(request, str(e))
        return _rerender()

    messages.success(
        request,
        f'"{product.name}" created and added to your store. '
        f'Set prices and stock to make it visible to members. '
        f'It will appear in the shared catalog for other gyms once approved.'
    )
    return redirect('gym_product_edit', gym_product_id=gym_product.id)


# ──────────────────────────────────────────────────────────────────────────────
# Manage already-imported GymProducts (pricing/stock/visibility)
# ──────────────────────────────────────────────────────────────────────────────

@gym_staff_required
@store_enabled_required
def gym_store_manage(request):
    """List everything this gym has imported, for editing. Supports
    search by name/brand/category/flavor/weight/barcode/SKU (Feature 10)."""
    gym = getattr(request, 'gym', None)
    query = request.GET.get('q', '').strip()

    if query:
        products = ProductSearchService.search_gym_store_admin(gym, query)
    else:
        products = (
            GymProduct.objects.filter(gym=gym, active=True)  # CHANGED — exclude soft-deleted
            .select_related('global_product')
            .prefetch_related('flavors__global_flavor')
            .order_by('display_order', 'id')
        )

    return render(request, 'shop/gym_store_manage.html', {
        'gym': gym, 'products': products, 'query': query,
    })


@gym_staff_required
@store_enabled_required
def gym_product_edit(request, gym_product_id):
    """
    Edit a gym's own copy: custom description, visibility, display order,
    and per-flavor pricing/stock/barcode/sku. Cannot touch master data
    (name, brand, category, images, flavor names) — those are read-only here.
    """
    gym = getattr(request, 'gym', None)
    gym_product = get_object_or_404(
        GymProduct.objects.select_related('global_product')
        .prefetch_related('flavors__global_flavor'),
        id=gym_product_id, gym=gym,
    )

    if request.method == 'POST':
        product_form = GymProductEditForm(request.POST, instance=gym_product)
        flavor_forms = []
        all_valid = product_form.is_valid()

        for flavor in gym_product.flavors.all():
            prefix = f'flavor_{flavor.id}'
            ff = GymProductFlavorEditForm(request.POST, instance=flavor, prefix=prefix)
            flavor_forms.append((flavor, ff))
            if not ff.is_valid():
                all_valid = False

        if all_valid:
            product_form.save()
            for _, ff in flavor_forms:
                ff.save()
            messages.success(request, "Product updated.")
            return redirect('gym_store_manage')

        messages.error(request, "Please correct the errors below.")
        return render(request, 'shop/gym_product_edit.html', {
            'gym': gym,
            'gym_product': gym_product,
            'product_form': product_form,
            'flavor_forms': flavor_forms,
        })

    product_form = GymProductEditForm(instance=gym_product)
    flavor_forms = [
        (flavor, GymProductFlavorEditForm(instance=flavor, prefix=f'flavor_{flavor.id}'))
        for flavor in gym_product.flavors.all()
    ]

    return render(request, 'shop/gym_product_edit.html', {
        'gym': gym,
        'gym_product': gym_product,
        'product_form': product_form,
        'flavor_forms': flavor_forms,
    })


@gym_staff_required
@store_enabled_required
@require_POST
def gym_product_remove(request, gym_product_id):
    """
    CHANGED (Feature 14): always soft-deletes now. GymProduct rows are
    never hard-deleted from this screen — order history and inventory
    movement history both stay intact and queryable. The old hard-delete
    path is removed since it's now unreachable through normal UI flow.
    """
    gym = getattr(request, 'gym', None)
    gym_product = get_object_or_404(GymProduct, id=gym_product_id, gym=gym)

    ProductImportService.deactivate(gym_product)
    messages.success(request, f'"{gym_product.global_product.name}" removed from your store.')
    return redirect('gym_store_manage')

@gym_staff_required
@store_enabled_required
@require_POST
def gym_product_restore(request, gym_product_id):
    """NEW — undo a soft delete."""
    gym = getattr(request, 'gym', None)
    gym_product = get_object_or_404(GymProduct, id=gym_product_id, gym=gym, active=False)

    ProductImportService.reactivate(gym_product)
    messages.success(
        request,
        f'"{gym_product.global_product.name}" restored. Review visibility before it goes live again.'
    )
    return redirect('gym_store_manage')

@gym_staff_required
@store_enabled_required
def stock_adjustment(request, flavor_id):
    """
    Gym owner increases/decreases stock for one flavor, with a mandatory
    reason for decreases. Every submission creates a GymInventoryMovement
    (Feature 3) and may trigger a low-stock notification (Feature 4).
    """
    gym = getattr(request, 'gym', None)
    flavor = get_object_or_404(
        GymProductFlavor.objects.select_related('gym_product__global_product', 'global_flavor'),
        id=flavor_id, gym_product__gym=gym,
    )

    if request.method == 'POST':
        form = StockAdjustmentForm(request.POST)
        if form.is_valid():
            qty    = form.cleaned_data['quantity']
            reason = form.cleaned_data['reason']
            adj_type = form.cleaned_data['adjustment_type']

            try:
                if adj_type == 'increase':
                    StockMovementService.record_purchase(flavor, qty, created_by=request.user, reason=reason)
                elif adj_type == 'decrease':
                    StockMovementService.record_adjustment(flavor, -qty, created_by=request.user, reason=reason)
                elif adj_type == 'damage':
                    StockMovementService.record_damage(flavor, qty, created_by=request.user, reason=reason)
                elif adj_type == 'expired':
                    StockMovementService.record_expired(flavor, qty, created_by=request.user, reason=reason)
                elif adj_type == 'returned':
                    StockMovementService.record_return(flavor, qty, created_by=request.user, reason=reason)

                messages.success(request, "Stock updated.")
                return redirect('stock_adjustment', flavor_id=flavor.id)
            except (ValidationError, DjangoValidationError) as e:
                messages.error(request, str(e))
    else:
        form = StockAdjustmentForm()

    history = StockMovementService.history_for(flavor)[:50]

    return render(request, 'shop/stock_adjustment.html', {
        'gym': gym,
        'flavor': flavor,
        'form': form,
        'history': history,
    })
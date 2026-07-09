# Shop/views.py

from decimal import Decimal
import logging
from django.db import models
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from cloudinary.utils import cloudinary_url
from Gym.decorators import store_enabled_required
from AuthFit.models import Enrollment
from Gym.mixins import gym_staff_required
from .models import Order, GymProduct, GymProductFlavor

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Membership eligibility guard
# ──────────────────────────────────────────────────────────────────────────────

def can_user_order_products(user, gym=None):
    enrollment = _get_enrollment(user, gym=gym)

    if enrollment is None:
        return False, "You must enroll in a membership plan before ordering products."

    if enrollment.is_expired:
        return False, "Your membership has expired. Please renew your plan to continue."

    if enrollment.paymentStatus != "Done":
        return False, "Complete your membership payment before ordering products."

    return True, enrollment


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_active_gym_product(gym, gym_product_id):
    """GymProduct is gym-scoped — always filter by gym to prevent IDOR.
    CHANGED (Feature 14): also excludes soft-deleted products."""
    return get_object_or_404(
        GymProduct.objects.filter(gym=gym, is_visible=True, active=True)
        .select_related('global_product')
        .prefetch_related('flavors__global_flavor'),
        id=gym_product_id,
    )


def _resolve_flavor(request, gym_product):
    flavor_id = request.POST.get('flavor')
    if not flavor_id or flavor_id == 'standard':
        return None, True
    try:
        return gym_product.flavors.select_related('global_flavor').get(id=int(flavor_id)), True
    except (GymProductFlavor.DoesNotExist, ValueError):
        messages.error(request, "Invalid flavour selected.")
        return None, False


def _soft_available(flavor):
    return flavor.available_stock if flavor else 0


def _validate_soft_stock(request, flavor, quantity):
    if flavor is None:
        messages.error(request, "Please select a flavour.")
        return False
    available = _soft_available(flavor)
    if quantity < 1:
        messages.error(request, "Quantity must be at least 1.")
        return False
    if quantity > available:
        messages.error(
            request,
            f"Only {available} unit(s) available."
            if available > 0 else
            "This item is currently out of stock."
        )
        return False
    return True


def _get_enrollment(user, gym=None):
    gym_pk    = gym.pk if gym else 'none'
    cache_key = f"enrollment_{user.id}_{gym_pk}"
    pk        = cache.get(cache_key)

    if pk is None:
        qs = Enrollment.objects.filter(user=user)
        if gym:
            qs = qs.filter(gym=gym)
        enrollment = qs.select_related('selectPlan', 'trainer').first()
        cache.set(cache_key, enrollment.pk if enrollment else 0, timeout=300)
        return enrollment

    if pk == 0:
        return None

    return (
        Enrollment.objects
        .filter(pk=pk)
        .select_related('selectPlan', 'trainer')
        .first()
    )


def _get_profile_image(user, enrollment):
    if not (enrollment and enrollment.face_image):
        return None
    image_url = cache.get(f"profile_image_{user.id}")
    if image_url is None:
        try:
            public_id = (
                enrollment.face_image.public_id
                if hasattr(enrollment.face_image, "public_id")
                else str(enrollment.face_image)
            )
            image_url, _ = cloudinary_url(
                public_id,
                width=130, height=130,
                crop="fill",
                secure=True,
            )
            cache.set(f"profile_image_{user.id}", image_url, timeout=300)
        except Exception:
            logger.exception("Cloudinary URL error for user %s", user.id)
            image_url = None
    return image_url


def _status_counts(base_qs):
    counts = base_qs.values('status').annotate(n=Count('id'))
    return {row['status']: row['n'] for row in counts}


# ──────────────────────────────────────────────────────────────────────────────
# Product views — GymProduct is gym-scoped
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@store_enabled_required
def product_list(request):
    gym = getattr(request, 'gym', None)

    products = (
        GymProduct.objects
        .filter(gym=gym, is_visible=True, active=True, global_product__active=True)  # CHANGED
        .select_related('global_product')
        .prefetch_related('flavors__global_flavor')
        .order_by('display_order', 'id')
    )
    allowed, _ = can_user_order_products(request.user, gym=gym)
    return render(request, 'shop/product_list.html', {
        'products':          products,
        'gym':               gym,
        'can_order_products': allowed,
    })


@login_required
@store_enabled_required
def product_detail(request, product_id):
    gym = getattr(request, 'gym', None)

    gym_product = _get_active_gym_product(gym, product_id)
    allowed, result = can_user_order_products(request.user, gym=gym)
    return render(request, 'shop/product_detail.html', {
        'product':            gym_product,
        'gym':                gym,
        'can_order_products': allowed,
        'order_block_reason': result if not allowed else None,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Confirm order
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@store_enabled_required
def confirm_order(request, product_id):
    gym = getattr(request, 'gym', None)

    gym_product = _get_active_gym_product(gym, product_id)
    if request.method != 'POST':
        return redirect('product_detail', product_id=product_id)

    allowed, result = can_user_order_products(request.user, gym=gym)
    if not allowed:
        messages.error(request, result)
        return redirect('product_detail', product_id=product_id)
    enrollment = result

    flavor, ok = _resolve_flavor(request, gym_product)
    if not ok:
        return redirect('product_detail', product_id=product_id)

    quantity = int(request.POST.get('quantity', 1))
    if not _validate_soft_stock(request, flavor, quantity):
        return redirect('product_detail', product_id=product_id)

    unit_price  = flavor.final_price
    total_price = unit_price * Decimal(quantity)

    image_url = _get_profile_image(request.user, enrollment)

    return render(request, 'shop/confirm_order.html', {
        'product':     gym_product,
        'flavor':      flavor,
        'quantity':    quantity,
        'unit_price':  unit_price,
        'total_price': total_price,
        'enrollment':  enrollment,
        'image_url':   image_url,
        'gym':         gym,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Place order
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@store_enabled_required
@transaction.atomic
def place_order(request):
    if request.method != 'POST':
        return redirect('product_list')

    gym = getattr(request, 'gym', None)
    product_id  = request.POST.get('product_id')
    gym_product = _get_active_gym_product(gym, product_id)

    allowed, result = can_user_order_products(request.user, gym=gym)
    if not allowed:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': result}, status=403)
        messages.error(request, result)
        return redirect('product_detail', product_id=product_id)

    quantity = int(request.POST.get('quantity', 1))

    flavor, ok = _resolve_flavor(request, gym_product)
    if not ok:
        return redirect('product_detail', product_id=product_id)
    if flavor is None:
        messages.error(request, "Please select a flavour.")
        return redirect('product_detail', product_id=product_id)

    # ── Duplicate-submission guard ──────────────────────────────────────
    # Same user, same gym, same product+flavor+quantity within the last
    # 10 seconds = almost certainly a double-click or a refreshed/resubmitted
    # form, not a genuine second order. Redirect them to the existing order
    # instead of creating a new one.
    from django.utils import timezone
    from datetime import timedelta

    recent_duplicate = Order.objects.filter(
        gym=gym,
        user=request.user,
        gym_product=gym_product,
        gym_flavor_id=flavor.id,
        quantity=quantity,
        ordered_at__gte=timezone.now() - timedelta(seconds=10),
    ).order_by('-ordered_at').first()

    if recent_duplicate:
        messages.info(request, "This order was already placed.")
        return redirect('order_success', order_id=recent_duplicate.id)
    # ─────────────────────────────────────────────────────────────────────

    flavor = GymProductFlavor.objects.select_for_update().get(id=flavor.id)
    real_stock = flavor.stock

    if quantity < 1 or quantity > real_stock:
        messages.error(request, f"Sorry, only {real_stock} unit(s) left.")
        return redirect('product_detail', product_id=product_id)

    unit_price  = flavor.final_price
    total_price = unit_price * Decimal(quantity)

    order = Order.objects.create(
        gym=gym,
        user=request.user,
        gym_product=gym_product,
        gym_flavor=flavor,
        quantity=quantity,
        unit_price=unit_price,
        discount=0,
        total_price=total_price,
        status=Order.Status.PENDING,
    )

    from .services import StockMovementService
    StockMovementService.record_sale(flavor, quantity, order=order)

    from .notifications import notify_staff_new_order
    if gym and gym.enable_store:
        transaction.on_commit(lambda: notify_staff_new_order(order))

    return redirect('order_success', order_id=order.id)


@login_required
@store_enabled_required
def order_success(request, order_id):
    if request.method != 'GET':
        return redirect('product_list')

    gym = getattr(request, 'gym', None)

    qs = Order.objects.filter(
        id=order_id,
        user=request.user,
    ).select_related('gym_product__global_product', 'gym_flavor__global_flavor', 'gym')

    if gym:
        qs = qs.filter(gym=gym)

    order = get_object_or_404(qs)

    enrollment = _get_enrollment(request.user, gym=gym)
    image_url  = _get_profile_image(request.user, enrollment)

    return render(request, 'shop/order_success.html', {
        'order':      order,
        'enrollment': enrollment,
        'image_url':  image_url,
        'gym':        gym,
    })


# ──────────────────────────────────────────────────────────────────────────────
# My orders
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@store_enabled_required
def my_orders(request):
    gym = getattr(request, 'gym', None)

    qs = Order.objects.filter(user=request.user).select_related(
        'gym_product__global_product', 'gym_flavor__global_flavor'
    )
    if gym:
        qs = qs.filter(gym=gym)
    orders = qs.order_by('-ordered_at')

    enrollment = _get_enrollment(request.user, gym=gym)
    image_url  = _get_profile_image(request.user, enrollment)

    return render(request, 'shop/my_orders.html', {
        'orders':     orders,
        'enrollment': enrollment,
        'image_url':  image_url,
        'gym':        gym,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Staff order dashboard
# ──────────────────────────────────────────────────────────────────────────────

@gym_staff_required
@store_enabled_required
def order_dashboard(request):
    gym           = getattr(request, 'gym', None)
    status_filter = request.GET.get('status', 'Pending')
    search        = request.GET.get('q', '').strip()

    base_qs = Order.objects.select_related(
        'user', 'gym_product__global_product', 'gym_flavor__global_flavor', 'gym'
    )
    if gym:
        base_qs = base_qs.prefetch_related(
            models.Prefetch(
                'user__enrollment_set',
                queryset=Enrollment.objects.filter(gym=gym),
                to_attr='gym_enrollments'
            )
        ).filter(gym=gym)
    else:
        base_qs = base_qs.prefetch_related('user__enrollment_set')

    qs = base_qs.order_by('-ordered_at')

    if search:
        qs = qs.filter(
            Q(id__icontains=search) |
            Q(user__username__icontains=search) |
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search) |
            Q(gym_product__global_product__name__icontains=search)
        )
    elif status_filter:
        qs = qs.filter(status=status_filter)

    all_counts = _status_counts(base_qs)

    revenue_qs = base_qs.filter(status=Order.Status.DELIVERED)
    revenue    = revenue_qs.aggregate(total=Sum('total_price'))['total'] or 0

    return render(request, 'shop/admin_orders.html', {
        'orders':        qs,
        'status_filter': status_filter if not search else '',
        'search':        search,
        'all_counts':    all_counts,
        'revenue':       revenue,
        'Status':        Order.Status,
        'gym':           gym,
        'next_action': {
            Order.Status.PENDING:   ('Confirm — Item at Gym', Order.Status.CONFIRMED, 'confirm'),
            Order.Status.CONFIRMED: ('Mark Collected',        Order.Status.DELIVERED, 'deliver'),
        },
    })


# ──────────────────────────────────────────────────────────────────────────────
# Order status update
# ──────────────────────────────────────────────────────────────────────────────

@gym_staff_required
@store_enabled_required
@require_POST
def order_update(request, order_id):
    gym    = getattr(request, 'gym', None)
    action = request.POST.get('action')
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _error(msg, status=400):
        if is_ajax:
            return JsonResponse({'ok': False, 'error': msg}, status=status)
        messages.error(request, msg)
        return redirect('admin_orders')

    qs = Order.objects.all()
    if gym:
        qs = qs.filter(gym=gym)

    try:
        order = qs.get(id=order_id)
    except Order.DoesNotExist:
        return _error('Order not found.', status=404)

    TRANSITIONS = {
        'confirm': (Order.Status.PENDING,   Order.Status.CONFIRMED),
        'deliver': (Order.Status.CONFIRMED, Order.Status.DELIVERED),
        'cancel':  (None,                   Order.Status.CANCELLED),
    }

    if action not in TRANSITIONS:
        return _error('Invalid action.')

    expected_from, new_status = TRANSITIONS[action]

    if action == 'cancel' and order.status not in (
        Order.Status.PENDING, Order.Status.CONFIRMED
    ):
        return _error(f'Cannot cancel an order with status "{order.status}".')

    if expected_from and order.status != expected_from:
        return _error(f'Expected status "{expected_from}", got "{order.status}".')

    order.status = new_status
    order.save(update_fields=['status', 'updated_at'])

    if is_ajax:
        return JsonResponse({'ok': True, 'new_status': new_status})

    messages.success(request, f'Order #{order.id} updated to "{new_status}".')
    return redirect(request.META.get('HTTP_REFERER', 'admin_orders'))


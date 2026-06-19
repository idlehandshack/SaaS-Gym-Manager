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
from django.contrib.admin.views.decorators import staff_member_required
from cloudinary.utils import cloudinary_url

from AuthFit.models import Enrollment
from .models import Order, Product, ProductFlavor

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_active_product(product_id):
    """Products are platform-global (no gym FK) — no scoping needed."""
    return get_object_or_404(
        Product.objects.prefetch_related('flavors'),
        id=product_id, active=True,
    )


def _resolve_flavor(request, product):
    flavor_id = request.POST.get('flavor')
    if not flavor_id or flavor_id == 'standard':
        return None, True
    try:
        return product.flavors.get(id=int(flavor_id)), True
    except (ProductFlavor.DoesNotExist, ValueError):
        messages.error(request, "Invalid flavour selected.")
        return None, False


def _soft_available(flavor, product):
    return flavor.available_stock if flavor else product.get_available_stock()


def _validate_soft_stock(request, flavor, product, quantity):
    available = _soft_available(flavor, product)
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
    """
    Returns Enrollment for this user+gym.
    Stores only safe primitives in cache (not model instances —
    Cloudinary fields break on unpickling).
    Returns the live ORM object for template use; cache stores the PK only.
    """
    gym_pk    = gym.pk if gym else 'none'
    cache_key = f"enrollment_{user.id}_{gym_pk}"
    pk        = cache.get(cache_key)

    if pk is None:
        qs = Enrollment.objects.filter(user=user)
        if gym:
            qs = qs.filter(gym=gym)
        enrollment = qs.select_related('selectPlan', 'trainer').first()
        # Cache the PK (safe to pickle), not the instance
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
                secure=True,   # ← always HTTPS
            )
            cache.set(f"profile_image_{user.id}", image_url, timeout=300)
        except Exception:
            logger.exception("Cloudinary URL error for user %s", user.id)
            image_url = None
    return image_url


def _status_counts(base_qs):
    """Count orders per status from an already-scoped queryset."""
    counts = base_qs.values('status').annotate(n=Count('id'))
    return {row['status']: row['n'] for row in counts}


# ──────────────────────────────────────────────────────────────────────────────
# Product views — products are platform-global, no gym scoping needed
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def product_list(request):
    gym      = getattr(request, 'gym', None)
    products = Product.objects.filter(active=True).prefetch_related('flavors')
    return render(request, 'shop/product_list.html', {
        'products': products,
        'gym':      gym,        # FIX: pass gym so template can use gym.gym_name
    })


@login_required
def product_detail(request, product_id):
    gym     = getattr(request, 'gym', None)
    product = _get_active_product(product_id)
    return render(request, 'shop/product_detail.html', {
        'product': product,
        'gym':     gym,        # FIX: pass gym so template shows gym.gym_name
    })


# ──────────────────────────────────────────────────────────────────────────────
# Confirm order (GET → show summary before placing)
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def confirm_order(request, product_id):
    gym     = getattr(request, 'gym', None)
    product = _get_active_product(product_id)
    if request.method != 'POST':
        return redirect('product_detail', product_id=product_id)

    flavor, ok = _resolve_flavor(request, product)
    if not ok:
        return redirect('product_detail', product_id=product_id)

    quantity = int(request.POST.get('quantity', 1))
    if not _validate_soft_stock(request, flavor, product, quantity):
        return redirect('product_detail', product_id=product_id)

    unit_price  = flavor.final_price if flavor else product.discounted_price
    total_price = unit_price * Decimal(quantity)

    enrollment = _get_enrollment(request.user, gym=gym)
    image_url  = _get_profile_image(request.user, enrollment)

    return render(request, 'shop/confirm_order.html', {
        'product':     product,
        'flavor':      flavor,
        'quantity':    quantity,
        'unit_price':  unit_price,
        'total_price': total_price,
        'enrollment':  enrollment,
        'image_url':   image_url,
        'gym':         gym,  
    })


# ──────────────────────────────────────────────────────────────────────────────
# Place order (POST — atomic, decrements stock)
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@transaction.atomic
def place_order(request):
    if request.method != 'POST':
        return redirect('product_list')
 
    gym        = getattr(request, 'gym', None)
    product_id = request.POST.get('product_id')
    product    = _get_active_product(product_id)
    quantity   = int(request.POST.get('quantity', 1))
 
    flavor, ok = _resolve_flavor(request, product)
    if not ok:
        return redirect('product_detail', product_id=product_id)
 
    # Hard stock check with row lock
    if flavor:
        flavor     = ProductFlavor.objects.select_for_update().get(id=flavor.id)
        real_stock = flavor.stock
    else:
        flavors    = list(ProductFlavor.objects.select_for_update().filter(product=product))
        real_stock = sum(f.stock for f in flavors)
 
    if quantity < 1 or quantity > real_stock:
        messages.error(request, f"Sorry, only {real_stock} unit(s) left.")
        return redirect('product_detail', product_id=product_id)
 
    unit_price  = flavor.final_price if flavor else product.discounted_price
    total_price = unit_price * Decimal(quantity)
 
    order = Order.objects.create(
        gym=gym,
        user=request.user,
        product=product,
        flavor=flavor,
        quantity=quantity,
        total_price=total_price,
        status=Order.Status.PENDING,
    )
 
    if flavor:
        ProductFlavor.objects.filter(id=flavor.id).update(stock=flavor.stock - quantity)
 
    from .notifications import notify_staff_new_order
    transaction.on_commit(lambda: notify_staff_new_order(order))
 
    # FIX: PRG pattern — redirect to GET endpoint instead of rendering directly
    # Without this, browser refresh replays the POST and creates a duplicate order
    return redirect('order_success', order_id=order.id)

# In views.py — replace your order_success view

@login_required
def order_success(request, order_id):
    """
    GET-only success page. Safe to refresh — just re-fetches the existing order.
    POST to this URL is rejected to prevent any accidental re-submission.
    """
    if request.method != 'GET':
        return redirect('product_list')

    gym = getattr(request, 'gym', None)

    qs = Order.objects.filter(
        id=order_id,
        user=request.user,
    ).select_related('product', 'flavor', 'gym')

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
# My orders (member view)
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def my_orders(request):
    gym = getattr(request, 'gym', None)

    # Scope to this gym — member at two gyms sees only this gym's orders
    qs = Order.objects.filter(user=request.user).select_related('product', 'flavor')
    if gym:
        qs = qs.filter(gym=gym)
    orders = qs.order_by('-ordered_at')

    enrollment = _get_enrollment(request.user, gym=gym)
    image_url  = _get_profile_image(request.user, enrollment)

    return render(request, 'shop/my_orders.html', {
        'orders':     orders,
        'enrollment': enrollment,
        'image_url':  image_url,
        'gym' : gym,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Staff order dashboard
# ──────────────────────────────────────────────────────────────────────────────

@staff_member_required
def order_dashboard(request):

    gym           = getattr(request, 'gym', None)
    status_filter = request.GET.get('status', 'Pending')
    search        = request.GET.get('q', '').strip()
    print("USER:", request.user)
    print("AUTH:", request.user.is_authenticated)
    print("GYM:", gym)

    # Base queryset — scoped to gym
    base_qs = Order.objects.select_related('user', 'product', 'flavor', 'gym')\
    .prefetch_related(
        models.Prefetch(
            'user__enrollment_set',
            queryset=Enrollment.objects.filter(gym=gym),
            to_attr='gym_enrollments'
        )
    )
    if gym:
        base_qs = base_qs.filter(gym=gym)

    qs = base_qs.order_by('-ordered_at')

    if search:
        qs = qs.filter(
            Q(id__icontains=search) |
            Q(user__username__icontains=search) |
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search) |
            Q(product__name__icontains=search)
        )
    elif status_filter:
        qs = qs.filter(status=status_filter)

    # Status counts scoped to this gym — not all gyms
    all_counts = _status_counts(base_qs)

    # Revenue — delivered orders, this gym only
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
# Order status update (staff AJAX/POST)
# ──────────────────────────────────────────────────────────────────────────────

@staff_member_required
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

    # Scope to gym — prevents IDOR across gyms
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
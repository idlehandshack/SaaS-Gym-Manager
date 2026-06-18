# Shop/models.py

from decimal import Decimal
from functools import cached_property

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Sum
from cloudinary.models import CloudinaryField

from Gym.models import Gym
from Gym.mixins import GymManager


# ── constants ─────────────────────────────────────────────────────────────────

STOCK_BUFFER = 2  # Reserve this many units — never exposed to users


# ── Product and ProductFlavor — platform-global (shared across all gyms) ──────
#
# DESIGN DECISION: Products are NOT gym-scoped.
# The SaaS owner manages one product catalog that all gyms share.
# (e.g. whey protein, creatine — same products available at every gym)
#
# If you later need per-gym products, add:
#   gym = models.ForeignKey(Gym, null=True, blank=True, on_delete=CASCADE)
# and filter by gym in product views.

class Product(models.Model):
    name        = models.CharField(max_length=200)
    description = models.TextField()
    base_price  = models.DecimalField(max_digits=10, decimal_places=2)
    image       = CloudinaryField('image', blank=True, null=True)
    discount    = models.IntegerField(default=0)
    active      = models.BooleanField(default=True)

    @cached_property
    def discounted_price(self) -> Decimal:
        return self.base_price * (1 - Decimal(self.discount) / 100)

    @cached_property
    def discount_amount(self) -> Decimal:
        return self.base_price - self.discounted_price

    def get_total_stock(self) -> int:
        """Raw stock — sum of all flavors. Use for internal/admin purposes."""
        result = self.flavors.aggregate(total=Sum('stock'))['total']
        return result or 0

    def get_available_stock(self) -> int:
        """User-facing stock: raw stock minus the buffer. Always >= 0."""
        return max(0, self.get_total_stock() - STOCK_BUFFER)

    @property
    def in_stock(self) -> bool:
        if 'flavors' in self.__dict__.get('_prefetched_objects_cache', {}):
            return any(f.available_stock > 0 for f in self.flavors.all())
        return self.flavors.filter(stock__gt=STOCK_BUFFER).exists()

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class ProductFlavor(models.Model):
    product          = models.ForeignKey(Product, related_name='flavors', on_delete=models.CASCADE)
    name             = models.CharField(max_length=100)
    stock            = models.PositiveIntegerField(default=0)
    price_adjustment = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    discount         = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    @cached_property
    def final_price(self) -> Decimal:
        return self.product.discounted_price + self.price_adjustment

    @property
    def available_stock(self) -> int:
        return max(0, self.stock - STOCK_BUFFER)

    @property
    def in_stock(self) -> bool:
        return self.available_stock > 0

    def __str__(self):
        return f"{self.product.name} — {self.name}"

    class Meta:
        ordering = ['name']


# ── Order — gym-scoped ────────────────────────────────────────────────────────
#
# FIX: Order had no gym FK.
# This meant:
#   - notify_staff_new_order(order) couldn't scope staff notifications by gym
#   - Admin order list showed all gyms' orders to every gym owner
#   - A member at ironhouse could see fitzone's orders
#
# gym is set from request.gym in the place_order view — never from user input.

class Order(models.Model):
    class Status(models.TextChoices):
        PENDING   = 'Pending',   'Pending'
        CONFIRMED = 'Confirmed', 'Confirmed'
        DELIVERED = 'Delivered', 'Delivered'
        CANCELLED = 'Cancelled', 'Cancelled'

    # FIX: gym FK added — every order belongs to one tenant
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True,
    )

    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
    product     = models.ForeignKey(Product, on_delete=models.CASCADE)
    flavor      = models.ForeignKey(
        ProductFlavor, null=True, blank=True, on_delete=models.SET_NULL
    )
    quantity    = models.PositiveIntegerField()
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    status      = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
        db_index=True,
    )
    ordered_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    objects = GymManager()   # FIX: enables .for_gym(gym) scoping

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING

    @property
    def is_confirmed(self) -> bool:
        return self.status == self.Status.CONFIRMED

    @property
    def is_delivered(self) -> bool:
        return self.status == self.Status.DELIVERED

    @property
    def is_cancelled(self) -> bool:
        return self.status == self.Status.CANCELLED

    def __str__(self):
        return f"Order#{self.pk} — {self.user.username} x {self.product.name} @ {self.gym.gym_code}"

    class Meta:
        ordering = ['-ordered_at']


# ── StaffDevice — gym-scoped ──────────────────────────────────────────────────
#
# FIX: StaffDevice had no gym FK and no user FK.
# This meant:
#   - _get_staff_tokens() couldn't filter by gym — ALL staff at ALL gyms
#     received every order and enrollment notification
#   - No way to know which gym a device token belongs to
#   - No way to deactivate a specific staff member's token on logout
#
# user FK added so we can tie a token to a specific staff member.
# gym FK added so notifications are scoped per tenant.

class StaffDevice(models.Model):
    """
    FCM push token for a staff/owner device at a specific gym.
    One user can have multiple devices (phone + tablet etc.).
    """
    # FIX: gym FK — scopes which gym's notifications this device receives
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True,
    )

    # FIX: user FK — ties the token to a specific staff member
    # Allows deactivating on logout and scoping by role if needed
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='staff_devices',
        db_index=True,
    )

    fcm_token   = models.TextField(unique=True)
    device_name = models.CharField(max_length=120, blank=True)
    last_seen   = models.DateTimeField(auto_now=True)
    active      = models.BooleanField(default=True)

    objects = GymManager()   # FIX: enables .for_gym(gym) scoping

    def __str__(self):
        return (
            f"{self.user.username} — {self.device_name} "
            f"@ {self.gym.gym_code} "
            f"({'Active' if self.active else 'Inactive'})"
        )

    class Meta:
        ordering            = ['-last_seen']
        verbose_name        = 'Staff Device'
        verbose_name_plural = 'Staff Devices'
        indexes = [
            models.Index(fields=['gym', 'active']),   # common query: active tokens per gym
            models.Index(fields=['user', 'gym']),     # common query: devices per staff member
        ]
# Shop/models.py

from decimal import Decimal
from functools import cached_property

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Sum
from cloudinary.models import CloudinaryField

from Gym.models import Gym
from Gym.mixins import GymManager


STOCK_BUFFER = 2  # Reserve this many units — never exposed to users


# ── Product — platform-global (shared across all gyms) ───────────────────────

class Product(models.Model):
    name        = models.CharField(max_length=200)
    description = models.TextField()
    base_price  = models.DecimalField(max_digits=10, decimal_places=2)
    image       = CloudinaryField('image', blank=True, null=True)
    discount    = models.IntegerField(default=0)
    active      = models.BooleanField(default=True)

    @cached_property
    def discounted_price(self) -> Decimal:
        # FIX: base_price is None on unsaved admin add form → guard it
        if self.base_price is None:
            return Decimal('0')
        return self.base_price * (1 - Decimal(self.discount or 0) / 100)

    @cached_property
    def discount_amount(self) -> Decimal:
        # FIX: same guard — base_price is None on unsaved instances
        if self.base_price is None:
            return Decimal('0')
        return self.base_price - self.discounted_price

    def get_total_stock(self) -> int:
        """Raw stock across all flavors. Internal/admin use only."""
        # FIX: pk is None on unsaved instances — aggregate would crash
        if not self.pk:
            return 0
        result = self.flavors.aggregate(total=Sum('stock'))['total']
        return result or 0

    def get_available_stock(self) -> int:
        """User-facing stock: raw stock minus buffer. Always >= 0."""
        return max(0, self.get_total_stock() - STOCK_BUFFER)

    @property
    def in_stock(self) -> bool:
        # FIX: pk is None on unsaved instances — filter would crash
        if not self.pk:
            return False
        if 'flavors' in self.__dict__.get('_prefetched_objects_cache', {}):
            return any(f.available_stock > 0 for f in self.flavors.all())
        return self.flavors.filter(stock__gt=STOCK_BUFFER).exists()

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


# ── ProductFlavor — platform-global ──────────────────────────────────────────

class ProductFlavor(models.Model):
    product          = models.ForeignKey(Product, related_name='flavors', on_delete=models.CASCADE)
    name             = models.CharField(max_length=100)
    stock            = models.PositiveIntegerField(default=0)
    price_adjustment = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    discount         = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    @cached_property
    def final_price(self) -> Decimal:
        # FIX: product may not be saved yet (no pk) or base_price may be None
        if not self.product_id:
            return Decimal('0')
        if self.product.base_price is None:
            return Decimal('0')
        return self.product.discounted_price + (self.price_adjustment or Decimal('0'))

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

class Order(models.Model):
    class Status(models.TextChoices):
        PENDING   = 'Pending',   'Pending'
        CONFIRMED = 'Confirmed', 'Confirmed'
        DELIVERED = 'Delivered', 'Delivered'
        CANCELLED = 'Cancelled', 'Cancelled'

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

    objects = GymManager()

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

class StaffDevice(models.Model):
    """
    FCM push token for a staff/owner device at a specific gym.
    One user can have multiple devices (phone + tablet etc.).
    """
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True,
    )
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

    objects = GymManager()

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
            models.Index(fields=['gym', 'active']),
            models.Index(fields=['user', 'gym']),
        ]
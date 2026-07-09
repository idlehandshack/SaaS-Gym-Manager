# Shop/models.py

from decimal import Decimal
from functools import cached_property

from django.contrib.auth.models import User
from django.db import models
from cloudinary.models import CloudinaryField

from Gym.models import Gym
from Gym.mixins import GymManager


# ── GlobalProduct — shared master catalog ────────────────────────────────────

class GlobalProduct(models.Model):
    class Approval(models.TextChoices):
        PENDING  = 'Pending',  'Pending'
        APPROVED = 'Approved', 'Approved'
        REJECTED = 'Rejected', 'Rejected'

    brand           = models.CharField(max_length=100, blank=True, db_index=True)
    category        = models.CharField(max_length=100, blank=True, db_index=True)
    name            = models.CharField(max_length=200, db_index=True)
    slug            = models.SlugField(max_length=220, unique=True)
    description     = models.TextField(blank=True)
    image           = CloudinaryField('image', blank=True, null=True)
    active          = models.BooleanField(default=True)
    approval_status = models.CharField(
        max_length=10, choices=Approval.choices, default=Approval.PENDING, db_index=True,
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='global_products_created',
    )
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='global_products_approved',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            base = slugify(f"{self.brand}-{self.name}") or slugify(self.name)
            slug, i = base, 1
            while GlobalProduct.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                i += 1
                slug = f"{base}-{i}"
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def is_approved(self) -> bool:
        return self.approval_status == self.Approval.APPROVED

    def __str__(self):
        return f"{self.brand} {self.name}".strip()

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['approval_status', 'active']),
            models.Index(fields=['brand', 'category']),
        ]


class GlobalProductFlavor(models.Model):
    global_product = models.ForeignKey(
        GlobalProduct, related_name='flavors', on_delete=models.CASCADE,
    )
    flavor_name = models.CharField(max_length=100)
    weight      = models.CharField(max_length=50, blank=True)
    image       = CloudinaryField('image', blank=True, null=True)

    def __str__(self):
        parts = [self.flavor_name, self.weight]
        return " ".join(p for p in parts if p)

    class Meta:
        ordering = ['flavor_name', 'weight']
        indexes = [models.Index(fields=['global_product'])]


# ── GymProduct — per-gym import of a GlobalProduct ───────────────────────────

class GymProduct(models.Model):
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True)
    global_product = models.ForeignKey(
        GlobalProduct, on_delete=models.CASCADE, related_name='gym_products',
    )
    custom_description = models.TextField(blank=True)
    display_order       = models.PositiveIntegerField(default=0)
    is_visible          = models.BooleanField(default=True)
    active = models.BooleanField(
        default=True,
        help_text="Soft-delete flag. False = removed from this gym's store but order history preserved.",
    )
    created_at           = models.DateTimeField(auto_now_add=True)

    objects = GymManager()

    @property
    def name(self) -> str:
        return self.global_product.name

    @property
    def description(self) -> str:
        return self.custom_description or self.global_product.description

    def __str__(self):
        return f"{self.global_product.name} @ {self.gym.gym_code}"

    class Meta:
        ordering = ['display_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['gym', 'global_product'],
                name='unique_gym_global_product',
            )
        ]
        indexes = [
            models.Index(fields=['gym', 'is_visible']),
            models.Index(fields=['gym', 'active']),  # NEW
        ]


class GymProductFlavor(models.Model):
    gym_product   = models.ForeignKey(GymProduct, related_name='flavors', on_delete=models.CASCADE ,null=True,
    blank=True,)
    global_flavor = models.ForeignKey(GlobalProductFlavor, on_delete=models.CASCADE)

    selling_price  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    discount_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cost_price     = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    stock          = models.PositiveIntegerField(default=0)
    sku            = models.CharField(max_length=64, blank=True)
    minimum_stock  = models.PositiveIntegerField(default=0)
    active         = models.BooleanField(default=True)
    low_stock_notified = models.BooleanField(
        default=False,
        help_text="Internal flag — prevents repeat low-stock alerts until stock recovers.",
    )
    STOCK_BUFFER = 2

    @cached_property
    def final_price(self) -> Decimal:
        if self.discount_price is not None:
            return self.discount_price
        return self.selling_price or Decimal('0')

    @property
    def available_stock(self) -> int:
        return max(0, self.stock - self.STOCK_BUFFER)

    @property
    def in_stock(self) -> bool:
        return self.available_stock > 0

    def __str__(self):
        return f"{self.gym_product} — {self.global_flavor.flavor_name}"
    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}

        if self.selling_price is not None and self.selling_price < 0:
            errors['selling_price'] = "Selling price cannot be negative."

        if self.discount_price is not None and self.discount_price < 0:
            errors['discount_price'] = "Discount price cannot be negative."

        if self.cost_price is not None and self.cost_price < 0:
            errors['cost_price'] = "Cost price cannot be negative."

        if (
            self.discount_price is not None
            and self.selling_price is not None
            and self.discount_price > self.selling_price
        ):
            errors['discount_price'] = "Discount price cannot be greater than selling price."

        if self.stock < 0:
            errors['stock'] = "Stock cannot be negative."

        if self.minimum_stock < 0:
            errors['minimum_stock'] = "Minimum stock cannot be negative."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean(validate_unique=False)
        super().save(*args, **kwargs)
    class Meta:
        ordering = ['global_flavor__flavor_name']
        constraints = [
            models.UniqueConstraint(
                fields=['gym_product', 'global_flavor'],
                name='unique_gym_product_flavor',
            ),
            models.CheckConstraint(
                check=models.Q(stock__gte=0),
                name='gymproductflavor_stock_gte_0',
            ),
            models.CheckConstraint(
                check=models.Q(minimum_stock__gte=0),
                name='gymproductflavor_minimum_stock_gte_0',
            ),
            models.CheckConstraint(
                check=models.Q(selling_price__isnull=True) | models.Q(selling_price__gte=0),
                name='gymproductflavor_selling_price_gte_0',
            ),
            models.CheckConstraint(
                check=models.Q(cost_price__isnull=True) | models.Q(cost_price__gte=0),
                name='gymproductflavor_cost_price_gte_0',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(discount_price__isnull=True)
                    | models.Q(selling_price__isnull=True)
                    | models.Q(discount_price__lte=models.F('selling_price'))
                ),
                name='gymproductflavor_discount_lte_selling',
            ),
        ]
        indexes = [
            models.Index(fields=['sku']),
            models.Index(fields=['gym_product', 'active']),
        ]


# ── Order — gym-scoped, points at gym's own product/flavor ──────────────────

class Order(models.Model):
    class Status(models.TextChoices):
        PENDING   = 'Pending',   'Pending'
        CONFIRMED = 'Confirmed', 'Confirmed'
        DELIVERED = 'Delivered', 'Delivered'
        CANCELLED = 'Cancelled', 'Cancelled'

    gym  = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')

    gym_product = models.ForeignKey(GymProduct, on_delete=models.PROTECT,null=True,
    blank=True,)
    gym_flavor  = models.ForeignKey(
        GymProductFlavor, null=True, blank=True, on_delete=models.SET_NULL,
    )

    quantity    = models.PositiveIntegerField()
    unit_price  = models.DecimalField(max_digits=10, decimal_places=2)
    discount    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True,
    )
    ordered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
        return f"Order#{self.pk} — {self.user.username} @ {self.gym.gym_code}"

    class Meta:
        ordering = ['-ordered_at']
        indexes = [
            models.Index(fields=['gym', 'status']),
            models.Index(fields=['gym_product', 'status']),
        ]

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



class GymInventoryMovement(models.Model):
    """
    Immutable audit log of every stock change on a GymProductFlavor.
    stock on GymProductFlavor is a denormalized "current total" that must
    only ever be changed through StockMovementService — never edited
    directly — so this log always reconstructs the full history.
    """

    class MovementType(models.TextChoices):
        PURCHASE   = 'Purchase',   'Purchase'
        SALE       = 'Sale',       'Sale'
        ADJUSTMENT = 'Adjustment', 'Adjustment'
        DAMAGE     = 'Damage',     'Damage'
        EXPIRED    = 'Expired',    'Expired'
        RETURNED   = 'Returned',   'Returned'

    gym_product_flavor = models.ForeignKey(
        GymProductFlavor, on_delete=models.CASCADE, related_name='movements',
    )
    # quantity is signed: positive = stock increase, negative = stock decrease.
    # e.g. Sale = -3, Purchase = +50, Damage = -2, Returned = +1
    quantity = models.IntegerField()
    movement_type = models.CharField(max_length=20, choices=MovementType.choices, db_index=True)
    reason     = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='inventory_movements',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # Denormalized snapshot for fast history display without recomputation
    stock_after = models.PositiveIntegerField()

    def __str__(self):
        sign = '+' if self.quantity >= 0 else ''
        return f"{self.gym_product_flavor} {sign}{self.quantity} ({self.movement_type})"

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['gym_product_flavor', '-created_at']),
            models.Index(fields=['movement_type']),
        ]
        verbose_name = 'Inventory Movement'
        verbose_name_plural = 'Inventory Movements'
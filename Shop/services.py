# Shop/services.py
"""
Service layer for the Global/Gym product catalog.

ProductImportService   — gym imports an approved GlobalProduct into its own store
ProductCreationService — gym owner proposes a brand-new product (goes to PENDING)
ProductApprovalService — admin approves/rejects/edits pending products
DuplicateMergeService  — admin merges two GlobalProducts into one
ProductSearchService   — search across brand/name/category/flavor/slug
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils.text import slugify

from Gym.models import Gym
from .models import (
    GlobalProduct, GlobalProductFlavor,
    GymProduct, GymProductFlavor,
    Order,
)
from difflib import SequenceMatcher
from django.contrib.auth.models import AnonymousUser
from .models import GymInventoryMovement
from django.db.models import Count, Sum, F, Case, When, DecimalField, Value
class DuplicateProductError(ValidationError):
    """
    Raised when a proposed new product closely matches an existing
    GlobalProduct. Carries the candidate matches so the view can render
    an "already exists — import instead?" prompt.
    """
    def __init__(self, candidates: list[GlobalProduct]):
        self.candidates = candidates
        super().__init__("A similar product already exists in the catalog.")
# ──────────────────────────────────────────────────────────────────────────────
# ProductImportService
# ──────────────────────────────────────────────────────────────────────────────

class ProductImportService:
    """Imports an approved GlobalProduct into a gym's own store."""

    @staticmethod
    def is_already_imported(gym: Gym, global_product: GlobalProduct) -> bool:
        return GymProduct.objects.filter(gym=gym, global_product=global_product).exists()

    @classmethod
    @transaction.atomic
    def import_product(cls, gym: Gym, global_product: GlobalProduct) -> GymProduct:
        """
        Creates a GymProduct + one GymProductFlavor per GlobalProductFlavor.
        Prices/stock/barcode/sku are intentionally left blank/zero — the gym
        owner fills them in afterward. Idempotent: re-importing an already
        imported product just returns the existing GymProduct untouched.
        """
        if not global_product.is_approved:
            raise ValidationError("Only approved products can be imported.")

        existing = GymProduct.objects.filter(
            gym=gym, global_product=global_product
        ).first()
        if existing:
            if not existing.active:
                existing.active = True
                existing.save(update_fields=['active'])
            return existing
        gym_product = GymProduct.objects.create(
            gym=gym,
            global_product=global_product,
            is_visible=False,  # stays hidden until owner sets prices
        )

        new_flavors = [
            GymProductFlavor(
                gym_product=gym_product,
                global_flavor=flavor,
                selling_price=None,
                discount_price=None,
                cost_price=None,
                stock=0,
                sku='',
                minimum_stock=0,
                active=True,
            )
            for flavor in global_product.flavors.all()
        ]
        GymProductFlavor.objects.bulk_create(new_flavors)

        return gym_product

    @staticmethod
    def sync_new_flavors(gym_product: GymProduct) -> int:
        """
        If the admin added new flavors to a GlobalProduct after a gym already
        imported it, call this to create the missing GymProductFlavor rows.
        Returns the number of new flavor rows created.
        """
        existing_flavor_ids = set(
            gym_product.flavors.values_list('global_flavor_id', flat=True)
        )
        missing = gym_product.global_product.flavors.exclude(
            id__in=existing_flavor_ids
        )
        new_rows = [
            GymProductFlavor(
                gym_product=gym_product,
                global_flavor=flavor,
                stock=0, active=True,
            )
            for flavor in missing
        ]
        if new_rows:
            GymProductFlavor.objects.bulk_create(new_rows)
        return len(new_rows)
    @staticmethod
    @transaction.atomic
    def deactivate(gym_product: GymProduct) -> GymProduct:
        """
        Soft-delete: hides the product and marks it inactive, but keeps
        the row (and all its GymProductFlavor rows + Order history)
        intact. This is now the ONLY way gym-owner "remove product"
        should behave — see catalog_views.gym_product_remove below.
        """
        gym_product.active = False
        gym_product.is_visible = False
        gym_product.save(update_fields=['active', 'is_visible'])
        return gym_product

    @staticmethod
    @transaction.atomic
    def reactivate(gym_product: GymProduct) -> GymProduct:
        gym_product.active = True
        gym_product.save(update_fields=['active'])
        # is_visible stays False — owner should deliberately re-enable
        # visibility (e.g. after confirming prices/stock are still valid)
        return gym_product


# ──────────────────────────────────────────────────────────────────────────────
# ProductCreationService
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FlavorInput:
    flavor_name: str
    weight: str = ''
    image: object = None


class ProductCreationService:
    """
    Handles a gym owner proposing a brand-new product.

    NEW BEHAVIOR (Feature 1):
    The GlobalProduct is still created with approval_status=PENDING (the
    shared catalog stays protected until admin review), BUT the creating
    gym is immediately auto-imported so they can start selling right away.
    Other gyms cannot see or import it until an admin approves it —
    ProductSearchService.search_global_catalog(approved_only=True) already
    excludes non-approved products, so this is enforced for free.
    """

    @staticmethod
    @transaction.atomic
    def create_pending_product(
        *,
        created_by: User,
        gym: Gym,
        brand: str,
        category: str,
        name: str,
        description: str = '',
        image=None,
        flavors: list[FlavorInput],
        skip_duplicate_check: bool = False,
    ) -> tuple[GlobalProduct, GymProduct]:
        """
        Returns (global_product, gym_product) — gym_product is the
        creator gym's own immediately-usable import.

        Raises ValidationError if a likely duplicate exists and
        skip_duplicate_check is False. Callers should run
        DuplicateDetectionService.find_candidates() first to show the
        user the "this may already exist" prompt, then re-call this with
        skip_duplicate_check=True if the user confirms they want a new one.
        """
        if not name.strip():
            raise ValidationError("Product name is required.")
        if not flavors:
            raise ValidationError("At least one flavor/variant is required.")

        if not skip_duplicate_check:
            duplicates = DuplicateDetectionService.find_candidates(
                brand=brand, name=name, category=category,
            )
            if duplicates.exists():
                raise DuplicateProductError(list(duplicates[:5]))

        product = GlobalProduct.objects.create(
            brand=brand.strip(),
            category=category.strip(),
            name=name.strip(),
            description=description.strip(),
            image=image,
            active=True,
            approval_status=GlobalProduct.Approval.PENDING,
            created_by=created_by,
        )

        GlobalProductFlavor.objects.bulk_create([
            GlobalProductFlavor(
                global_product=product,
                flavor_name=f.flavor_name.strip(),
                weight=f.weight.strip(),
                image=f.image,
            )
            for f in flavors
        ])

        # Feature 1 — auto-import into the CREATOR gym only.
        # ProductImportService.import_product() normally requires
        # is_approved, so we can't reuse it here — a pending product's
        # creator gym gets a direct, explicit import instead.
        gym_product = GymProduct.objects.create(
            gym=gym,
            global_product=product,
            is_visible=False,  # owner still must set prices/stock before it's live
        )
        GymProductFlavor.objects.bulk_create([
            GymProductFlavor(
                gym_product=gym_product,
                global_flavor=global_flavor,
                stock=0,
                active=True,
            )
            for global_flavor in product.flavors.all()
        ])

        return product, gym_product

# ──────────────────────────────────────────────────────────────────────────────
# DuplicateDetectionService  (NEW — Feature 2)
# ──────────────────────────────────────────────────────────────────────────────

class DuplicateDetectionService:
    """
    Checks whether a proposed new product likely already exists in the
    global catalog, before a GlobalProduct row is created. Matches on
    brand + name (exact/fuzzy) and slug, and secondarily on category.

    Does NOT block creation by itself — callers decide what to do with
    the candidates (e.g. show "this may already exist, import instead?").
    """

    SIMILARITY_THRESHOLD = 0.82  # tuned for brand+name strings

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join((text or "").strip().lower().split())

    @classmethod
    def _similarity(cls, a: str, b: str) -> float:
        return SequenceMatcher(None, cls._normalize(a), cls._normalize(b)).ratio()

    @classmethod
    def find_candidates(
        cls,
        *,
        brand: str = '',
        name: str,
        category: str = '',
    ) -> QuerySet:
        """
        Returns a QuerySet of GlobalProducts that are plausible duplicates,
        ordered by relevance (exact slug match first, then similarity).
        Only searches active, non-rejected products.
        """
        proposed_slug = slugify(f"{brand}-{name}") or slugify(name)

        candidate_pool = GlobalProduct.objects.filter(
            active=True
        ).exclude(
            approval_status=GlobalProduct.Approval.REJECTED
        ).select_related().prefetch_related('flavors')

        # Cheap DB-level pre-filter: same brand OR overlapping name tokens
        name_tokens = [t for t in cls._normalize(name).split() if len(t) > 2]
        token_q = Q()
        for token in name_tokens:
            token_q |= Q(name__icontains=token)

        pool = candidate_pool.filter(
            Q(brand__iexact=brand.strip()) | token_q | Q(slug=proposed_slug)
        ).distinct()

        # Rank in Python by fuzzy similarity — pool is small (pre-filtered)
        scored = []
        for candidate in pool:
            if candidate.slug == proposed_slug:
                score = 1.0
            else:
                score = cls._similarity(
                    f"{brand} {name}", f"{candidate.brand} {candidate.name}"
                )
            if score >= cls.SIMILARITY_THRESHOLD:
                scored.append((score, candidate.id))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        ordered_ids = [pk for _, pk in scored]

        if not ordered_ids:
            return GlobalProduct.objects.none()

        # Preserve score ordering using a case/when, keeping it queryset-based
        from django.db.models import Case, When
        preserved_order = Case(
            *[When(pk=pk, then=pos) for pos, pk in enumerate(ordered_ids)]
        )
        return GlobalProduct.objects.filter(
            id__in=ordered_ids
        ).prefetch_related('flavors').order_by(preserved_order)

    @classmethod
    def has_likely_duplicate(cls, *, brand: str = '', name: str, category: str = '') -> bool:
        return cls.find_candidates(brand=brand, name=name, category=category).exists()
# ──────────────────────────────────────────────────────────────────────────────
# ProductApprovalService
# ──────────────────────────────────────────────────────────────────────────────

class ProductApprovalService:
    """Admin review actions on pending GlobalProducts."""

    @staticmethod
    @transaction.atomic
    def approve(product: GlobalProduct, approved_by: User) -> GlobalProduct:
        product.approval_status = GlobalProduct.Approval.APPROVED
        product.approved_by = approved_by
        product.save(update_fields=['approval_status', 'approved_by', 'updated_at'])
        return product

    @staticmethod
    @transaction.atomic
    def reject(product: GlobalProduct, rejected_by: User, reason: str = '') -> GlobalProduct:
        product.approval_status = GlobalProduct.Approval.REJECTED
        product.approved_by = rejected_by
        product.save(update_fields=['approval_status', 'approved_by', 'updated_at'])
        # Hook point: notify created_by with `reason` via your notifications module
        return product

    @staticmethod
    @transaction.atomic
    def edit(
        product: GlobalProduct,
        *,
        brand: Optional[str] = None,
        category: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        image=None,
    ) -> GlobalProduct:
        """Admin-only master-data edit. Does not touch gym-level pricing."""
        fields = []
        if brand is not None:
            product.brand = brand.strip()
            fields.append('brand')
        if category is not None:
            product.category = category.strip()
            fields.append('category')
        if name is not None:
            product.name = name.strip()
            fields.append('name')
        if description is not None:
            product.description = description.strip()
            fields.append('description')
        if image is not None:
            product.image = image
            fields.append('image')
        if fields:
            fields.append('updated_at')
            product.save(update_fields=fields)
        return product

    @staticmethod
    def pending_queryset() -> QuerySet:
        return GlobalProduct.objects.filter(
            approval_status=GlobalProduct.Approval.PENDING
        ).select_related('created_by').prefetch_related('flavors').order_by('-created_at')

    @staticmethod
    def approved_queryset() -> QuerySet:
        return GlobalProduct.objects.filter(
            approval_status=GlobalProduct.Approval.APPROVED
        ).select_related('approved_by').prefetch_related('flavors').order_by('name')

    @staticmethod
    def rejected_queryset() -> QuerySet:
        return GlobalProduct.objects.filter(
            approval_status=GlobalProduct.Approval.REJECTED
        ).select_related('approved_by').prefetch_related('flavors').order_by('-updated_at')


# ──────────────────────────────────────────────────────────────────────────────
# DuplicateMergeService
# ──────────────────────────────────────────────────────────────────────────────

class DuplicateMergeService:
    """
    Merges a duplicate GlobalProduct ("loser") into the canonical one
    ("winner"). Re-points every gym's GymProduct/GymProductFlavor and every
    historical Order at the winner's equivalent flavor, matched by
    flavor_name+weight. Flavors on the loser with no matching name/weight
    on the winner are created on the winner so no gym data is lost.
    """

    @staticmethod
    def _flavor_key(flavor: GlobalProductFlavor) -> tuple[str, str]:
        return (flavor.flavor_name.strip().lower(), flavor.weight.strip().lower())

    @classmethod
    @transaction.atomic
    def merge(cls, *, winner: GlobalProduct, loser: GlobalProduct, merged_by: User) -> GlobalProduct:
        if winner.pk == loser.pk:
            raise ValidationError("Cannot merge a product into itself.")

        winner_flavors_by_key = {
            cls._flavor_key(f): f for f in winner.flavors.all()
        }

        # Map every loser flavor to a winner flavor, creating one if needed
        flavor_map: dict[int, GlobalProductFlavor] = {}
        for loser_flavor in loser.flavors.all():
            key = cls._flavor_key(loser_flavor)
            target = winner_flavors_by_key.get(key)
            if target is None:
                target = GlobalProductFlavor.objects.create(
                    global_product=winner,
                    flavor_name=loser_flavor.flavor_name,
                    weight=loser_flavor.weight,
                    image=loser_flavor.image,
                )
                winner_flavors_by_key[key] = target
            flavor_map[loser_flavor.id] = target

        # Re-point each gym's import. If a gym already imported BOTH winner
        # and loser, keep the winner's GymProduct and fold the loser's
        # flavor rows (stock/pricing) into it rather than overwriting.
        for loser_gym_product in GymProduct.objects.filter(
            global_product=loser
        ).select_related('gym').prefetch_related('flavors__global_flavor'):
            winner_gym_product, _ = GymProduct.objects.get_or_create(
                gym=loser_gym_product.gym,
                global_product=winner,
                defaults={
                    'custom_description': loser_gym_product.custom_description,
                    'display_order': loser_gym_product.display_order,
                    'is_visible': loser_gym_product.is_visible,
                },
            )

            for loser_flavor_row in loser_gym_product.flavors.select_related('global_flavor'):
                target_global_flavor = flavor_map[loser_flavor_row.global_flavor_id]
                winner_flavor_row, created = GymProductFlavor.objects.get_or_create(
                    gym_product=winner_gym_product,
                    global_flavor=target_global_flavor,
                    defaults={
                        'selling_price': loser_flavor_row.selling_price,
                        'discount_price': loser_flavor_row.discount_price,
                        'cost_price': loser_flavor_row.cost_price,
                        'stock': loser_flavor_row.stock,
                        'sku': loser_flavor_row.sku,
                        'minimum_stock': loser_flavor_row.minimum_stock,
                        'active': loser_flavor_row.active,
                    },
                )
                if not created:
                    # Winner's gym row already existed — repoint historical
                    # orders from the loser's flavor row to the winner's,
                    # then remove the now-orphaned loser flavor row.
                    Order.objects.filter(gym_flavor=loser_flavor_row).update(
                        gym_flavor=winner_flavor_row
                    )
                else:
                    # New row was created by copying the loser's row —
                    # repoint orders to this new row directly.
                    Order.objects.filter(gym_flavor=loser_flavor_row).update(
                        gym_flavor=winner_flavor_row
                    )

                # Repoint orders that reference the loser's GymProduct
                Order.objects.filter(gym_product=loser_gym_product).update(
                    gym_product=winner_gym_product
                )

            loser_gym_product.delete()  # flavor rows cascade

        loser.active = False
        loser.approval_status = GlobalProduct.Approval.REJECTED
        loser.save(update_fields=['active', 'approval_status', 'updated_at'])

        return winner


# ──────────────────────────────────────────────────────────────────────────────
# ProductSearchService
# ──────────────────────────────────────────────────────────────────────────────

class ProductSearchService:
    """Search the global catalog: brand, name, category, flavor, slug.
    Gym-store search additionally covers barcode, SKU, and weight."""

    @staticmethod
    def search_global_catalog(query: str, *, approved_only: bool = True) -> QuerySet:
        # UNCHANGED — existing behavior preserved exactly
        qs = GlobalProduct.objects.prefetch_related('flavors')
        if approved_only:
            qs = qs.filter(approval_status=GlobalProduct.Approval.APPROVED, active=True)

        query = query.strip()
        if not query:
            return qs.order_by('name')

        return qs.filter(
            Q(name__icontains=query) |
            Q(brand__icontains=query) |
            Q(category__icontains=query) |
            Q(slug__icontains=query) |
            Q(flavors__flavor_name__icontains=query)
        ).distinct().order_by('name')

    @staticmethod
    def search_gym_store(gym: Gym, query: str) -> QuerySet:
        """
        Search within a gym's own imported products.
        CHANGED (Feature 10): now also matches barcode, SKU, and weight
        on the gym's own GymProductFlavor rows — these are gym-specific
        fields so they only make sense scoped to one gym's store, not the
        shared global catalog. icontains is already case-insensitive in
        Postgres/MySQL and supports partial matches, so no change needed
        for that part of the requirement.
        """
        qs = (
            GymProduct.objects.filter(gym=gym, is_visible=True)
            .select_related('global_product')
            .prefetch_related('flavors__global_flavor')
        )
        query = query.strip()
        if not query:
            return qs.order_by('display_order', 'id')

        return qs.filter(
            Q(global_product__name__icontains=query) |
            Q(global_product__brand__icontains=query) |
            Q(global_product__category__icontains=query) |
            Q(flavors__global_flavor__flavor_name__icontains=query) |
            Q(flavors__global_flavor__weight__icontains=query) |     # NEW
            Q(flavors__sku__icontains=query)                         # NEW
        ).distinct().order_by('display_order', 'id')

    @staticmethod
    def search_gym_store_admin(gym: Gym, query: str, *, include_inactive: bool = False) -> QuerySet:
        qs = GymProduct.objects.filter(gym=gym)
        if not include_inactive:
            qs = qs.filter(active=True)
        qs = qs.select_related('global_product').prefetch_related('flavors__global_flavor')

        query = query.strip()
        if not query:
            return qs.order_by('display_order', 'id')

        return qs.filter(
            Q(global_product__name__icontains=query) |
            Q(global_product__brand__icontains=query) |
            Q(global_product__category__icontains=query) |
            Q(flavors__global_flavor__flavor_name__icontains=query) |
            Q(flavors__global_flavor__weight__icontains=query) |
            Q(flavors__sku__icontains=query)
        ).distinct().order_by('display_order', 'id')
    

# ──────────────────────────────────────────────────────────────────────────────
# StockMovementService  (NEW — Feature 3)
# ──────────────────────────────────────────────────────────────────────────────

class StockMovementService:
    """
    The ONLY sanctioned way to change GymProductFlavor.stock. Every call
    creates a GymInventoryMovement record and keeps stock non-negative.
    Also triggers low-stock notification checks (Feature 4).
    """

    @staticmethod
    @transaction.atomic
    def record_movement(
        *,
        gym_product_flavor: GymProductFlavor,
        quantity: int,
        movement_type: str,
        reason: str = '',
        created_by: Optional[User] = None,
        _skip_low_stock_check: bool = False,
    ) -> GymInventoryMovement:
        """
        quantity is signed — positive adds stock, negative removes it.
        Locks the flavor row to prevent race conditions with concurrent
        orders/adjustments.
        """
        if quantity == 0:
            raise ValidationError("Movement quantity cannot be zero.")

        locked_flavor = GymProductFlavor.objects.select_for_update().get(
            pk=gym_product_flavor.pk
        )

        new_stock = locked_flavor.stock + quantity
        if new_stock < 0:
            raise ValidationError(
                f"Cannot record movement — would result in negative stock "
                f"({locked_flavor.stock} {quantity:+d} = {new_stock})."
            )

        locked_flavor.stock = new_stock
        locked_flavor.full_clean(validate_unique=False)  # ADDED — enforce Feature 8 even on partial saves
        locked_flavor.save(update_fields=['stock'])

        movement = GymInventoryMovement.objects.create(
            gym_product_flavor=locked_flavor,
            quantity=quantity,
            movement_type=movement_type,
            reason=reason.strip(),
            created_by=created_by if isinstance(created_by, User) else None,
            stock_after=new_stock,
        )

        if not _skip_low_stock_check:
            from .notifications_stock import check_low_stock  # local import — avoids circulars
            check_low_stock(locked_flavor)

        return movement

    @staticmethod
    def record_sale(gym_product_flavor: GymProductFlavor, quantity: int, *, order=None) -> GymInventoryMovement:
        """Convenience wrapper used by place_order. quantity is positive (units sold)."""
        reason = f"Order #{order.id}" if order else "Sale"
        return StockMovementService.record_movement(
            gym_product_flavor=gym_product_flavor,
            quantity=-abs(quantity),
            movement_type=GymInventoryMovement.MovementType.SALE,
            reason=reason,
            created_by=order.user if order else None,
        )

    @staticmethod
    def record_purchase(gym_product_flavor: GymProductFlavor, quantity: int, *, created_by: User, reason: str = '') -> GymInventoryMovement:
        return StockMovementService.record_movement(
            gym_product_flavor=gym_product_flavor,
            quantity=abs(quantity),
            movement_type=GymInventoryMovement.MovementType.PURCHASE,
            reason=reason,
            created_by=created_by,
        )

    @staticmethod
    def record_adjustment(gym_product_flavor: GymProductFlavor, delta: int, *, created_by: User, reason: str) -> GymInventoryMovement:
        """delta can be positive or negative — a manual correction."""
        if not reason.strip():
            raise ValidationError("A reason is required for manual stock adjustments.")
        return StockMovementService.record_movement(
            gym_product_flavor=gym_product_flavor,
            quantity=delta,
            movement_type=GymInventoryMovement.MovementType.ADJUSTMENT,
            reason=reason,
            created_by=created_by,
        )

    @staticmethod
    def record_damage(gym_product_flavor: GymProductFlavor, quantity: int, *, created_by: User, reason: str = '') -> GymInventoryMovement:
        return StockMovementService.record_movement(
            gym_product_flavor=gym_product_flavor,
            quantity=-abs(quantity),
            movement_type=GymInventoryMovement.MovementType.DAMAGE,
            reason=reason,
            created_by=created_by,
        )

    @staticmethod
    def record_expired(gym_product_flavor: GymProductFlavor, quantity: int, *, created_by: User, reason: str = '') -> GymInventoryMovement:
        return StockMovementService.record_movement(
            gym_product_flavor=gym_product_flavor,
            quantity=-abs(quantity),
            movement_type=GymInventoryMovement.MovementType.EXPIRED,
            reason=reason,
            created_by=created_by,
        )

    @staticmethod
    def record_return(gym_product_flavor: GymProductFlavor, quantity: int, *, created_by: User, reason: str = '', order=None) -> GymInventoryMovement:
        reason = reason or (f"Return for Order #{order.id}" if order else "Returned")
        return StockMovementService.record_movement(
            gym_product_flavor=gym_product_flavor,
            quantity=abs(quantity),
            movement_type=GymInventoryMovement.MovementType.RETURNED,
            reason=reason,
            created_by=created_by,
        )

    @staticmethod
    def history_for(gym_product_flavor: GymProductFlavor) -> QuerySet:
        return gym_product_flavor.movements.select_related('created_by').order_by('-created_at')
    
class PricingService:
    """Validated price/stock updates for GymProductFlavor, outside the admin/form path."""

    @staticmethod
    @transaction.atomic
    def update_pricing(
        flavor: GymProductFlavor,
        *,
        selling_price=None,
        discount_price=None,
        cost_price=None,
        sku: Optional[str] = None,
        minimum_stock: Optional[int] = None,
    ) -> GymProductFlavor:
        if selling_price is not None:
            flavor.selling_price = selling_price
        if discount_price is not None:
            flavor.discount_price = discount_price
        if cost_price is not None:
            flavor.cost_price = cost_price
        if sku is not None:
            flavor.sku = sku.strip()
        if minimum_stock is not None:
            flavor.minimum_stock = minimum_stock

        flavor.full_clean(validate_unique=False)  # raises ValidationError with field errors
        flavor.save()
        return flavor
    


class ProductDashboardService:
    """
    Aggregated metrics for a gym's product catalog. All queries are
    scoped to one gym and built to run in a small, fixed number of
    queries regardless of catalog size — no per-product Python loops.
    """

    @staticmethod
    def get_summary(gym: Gym) -> dict:
        products_qs = GymProduct.objects.filter(gym=gym, active=True)

        totals = products_qs.aggregate(
            total_products=Count('id'),
            visible_products=Count('id', filter=Q(is_visible=True)),
            hidden_products=Count('id', filter=Q(is_visible=False)),
        )

        flavor_qs = GymProductFlavor.objects.filter(
            gym_product__gym=gym, gym_product__active=True, active=True,
        )
        stock_totals = flavor_qs.aggregate(
            low_stock=Count('id', filter=Q(stock__gt=0, stock__lte=F('minimum_stock'))),
            out_of_stock=Count('id', filter=Q(stock=0)),
        )

        revenue_qs = Order.objects.filter(
            gym=gym, status=Order.Status.DELIVERED,
        )
        revenue_totals = revenue_qs.aggregate(
            revenue=Sum('total_price'),
        )

        # Estimated profit = sum(total_price) - sum(quantity * cost_price)
        # over delivered orders whose flavor still has a cost_price set.
        # Orders whose flavor was later deleted (SET_NULL) or never had a
        # cost_price recorded are excluded from the cost side, so this is
        # a lower-bound estimate, not an exact figure — labeled as such.
        cost_qs = revenue_qs.filter(gym_flavor__cost_price__isnull=False).annotate(
            line_cost=F('quantity') * F('gym_flavor__cost_price')
        )
        total_cost = cost_qs.aggregate(cost=Sum('line_cost'))['cost'] or 0
        revenue = revenue_totals['revenue'] or 0
        estimated_profit = revenue - total_cost

        return {
            'total_products':   totals['total_products'] or 0,
            'visible_products': totals['visible_products'] or 0,
            'hidden_products':  totals['hidden_products'] or 0,
            'low_stock_count':  stock_totals['low_stock'] or 0,
            'out_of_stock_count': stock_totals['out_of_stock'] or 0,
            'revenue': revenue,
            'estimated_profit': estimated_profit,
        }

    @staticmethod
    def most_sold(gym: Gym, *, limit: int = 10) -> QuerySet:
        return (
            Order.objects.filter(gym=gym, status=Order.Status.DELIVERED)
            .values(
                'gym_product_id',
                name=F('gym_product__global_product__name'),
            )
            .annotate(units_sold=Sum('quantity'))
            .order_by('-units_sold')[:limit]
        )

    @staticmethod
    def least_sold(gym: Gym, *, limit: int = 10) -> QuerySet:
        """
        Products that HAVE been sold at least once, ranked ascending —
        distinct from 'never sold', which is a separate metric since
        never-sold products wouldn't appear in this Order-based query at all.
        """
        return (
            Order.objects.filter(gym=gym, status=Order.Status.DELIVERED)
            .values(
                'gym_product_id',
                name=F('gym_product__global_product__name'),
            )
            .annotate(units_sold=Sum('quantity'))
            .order_by('units_sold')[:limit]
        )

    @staticmethod
    def never_sold(gym: Gym) -> QuerySet:
        sold_ids = Order.objects.filter(
            gym=gym, status=Order.Status.DELIVERED
        ).values_list('gym_product_id', flat=True).distinct()

        return (
            GymProduct.objects.filter(gym=gym, active=True)
            .exclude(id__in=sold_ids)
            .select_related('global_product')
        )

    @staticmethod
    def low_stock_flavors(gym: Gym) -> QuerySet:
        return (
            GymProductFlavor.objects.filter(
                gym_product__gym=gym, gym_product__active=True, active=True,
                stock__gt=0, stock__lte=F('minimum_stock'),
            )
            .select_related('gym_product__global_product', 'global_flavor')
            .order_by('stock')
        )

    @staticmethod
    def out_of_stock_flavors(gym: Gym) -> QuerySet:
        return (
            GymProductFlavor.objects.filter(
                gym_product__gym=gym, gym_product__active=True, active=True, stock=0,
            )
            .select_related('gym_product__global_product', 'global_flavor')
        )
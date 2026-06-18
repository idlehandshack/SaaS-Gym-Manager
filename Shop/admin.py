# Shop/admin.py

from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import Product, ProductFlavor, Order, StaffDevice


# ── Base admin for gym-scoped Shop models ─────────────────────────────────────
#
# Product and ProductFlavor are platform-global (no gym FK) — they use
# a simpler base that only gates on superuser.
#
# Order and StaffDevice are gym-scoped — they use GymScopedShopAdmin.

class GymScopedShopAdmin(admin.ModelAdmin):
    """
    Base for Order and StaffDevice — both have gym FK.
    Superuser sees all gyms. Gym staff sees only their gym.
    """

    def get_gym(self, request):
        return getattr(request, 'gym', None)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        gym = self.get_gym(request)
        if not gym:
            return qs.none()
        return qs.filter(gym=gym)

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if not request.user.is_superuser and 'gym' in fields:
            fields.remove('gym')
        return fields

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if request.user.is_superuser and 'gym' not in readonly:
            readonly.append('gym')
        return readonly

    def save_model(self, request, obj, form, change):
        gym = self.get_gym(request)
        if gym and not obj.gym_id:
            obj.gym = gym
        elif gym and obj.gym_id and obj.gym_id != gym.pk:
            raise PermissionDenied("You cannot modify records from another gym.")
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return True
        gym = self.get_gym(request)
        if gym and obj.gym_id != gym.pk:
            return False
        return True

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return True
        gym = self.get_gym(request)
        if gym and obj.gym_id != gym.pk:
            return False
        return True

    def get_list_display(self, request):
        cols = list(self.list_display)
        if request.user.is_superuser and 'gym' not in cols:
            cols.insert(0, 'gym')
        return cols


# ── Product — platform-global, superuser only ─────────────────────────────────
#
# Products have no gym FK — they're shared across all gyms.
# Only the SaaS superuser should manage the product catalog.
# Gym owners see products (read-only) but cannot add/edit/delete them.

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display   = ['name', 'base_price', 'discount', 'discounted_price_display', 'in_stock', 'active']
    list_filter    = ['active']
    search_fields  = ['name']
    ordering       = ['name']
    readonly_fields = ['discounted_price_display', 'discount_amount_display']

    @admin.display(description='Discounted Price')
    def discounted_price_display(self, obj):
        return f"Rs.{obj.discounted_price:.2f}"

    @admin.display(description='Discount Amount')
    def discount_amount_display(self, obj):
        return f"Rs.{obj.discount_amount:.2f}"

    def has_add_permission(self, request):
        # Only superuser can add products to the global catalog
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


# ── ProductFlavor — platform-global, superuser only ───────────────────────────

@admin.register(ProductFlavor)
class ProductFlavorAdmin(admin.ModelAdmin):
    list_display  = ['product', 'name', 'stock', 'available_stock_display', 'final_price_display', 'in_stock']
    list_filter   = ['product']
    search_fields = ['name', 'product__name']
    ordering      = ['product__name', 'name']

    @admin.display(description='Available Stock')
    def available_stock_display(self, obj):
        return obj.available_stock

    @admin.display(description='Final Price')
    def final_price_display(self, obj):
        return f"Rs.{obj.final_price:.2f}"

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
    
    def has_view_permission(self, request, obj=None):
        return True


# ── Order — gym-scoped ────────────────────────────────────────────────────────

@admin.register(Order)
class OrderAdmin(GymScopedShopAdmin):
    list_display  = [
        'id', 'user', 'product', 'flavor', 'quantity',
        'total_price', 'status', 'ordered_at',
    ]
    list_filter   = ['status']
    search_fields = ['user__username', 'product__name']
    ordering      = ['-ordered_at']
    readonly_fields = ['ordered_at', 'updated_at', 'total_price']

    # Gym staff can update order status (Pending→Confirmed→Delivered)
    # but cannot change the product, quantity, or price
    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            # Staff can only change status — everything else is locked
            for field in ['user', 'product', 'flavor', 'quantity']:
                if field not in readonly:
                    readonly.append(field)
        return readonly

    actions = ['mark_confirmed', 'mark_delivered', 'mark_cancelled']

    @admin.action(description='Mark selected orders as Confirmed')
    def mark_confirmed(self, request, queryset):
        # Scope action to current gym
        gym = self.get_gym(request)
        if gym:
            queryset = queryset.filter(gym=gym)
        queryset.update(status=Order.Status.CONFIRMED)

    @admin.action(description='Mark selected orders as Delivered')
    def mark_delivered(self, request, queryset):
        gym = self.get_gym(request)
        if gym:
            queryset = queryset.filter(gym=gym)
        queryset.update(status=Order.Status.DELIVERED)

    @admin.action(description='Mark selected orders as Cancelled')
    def mark_cancelled(self, request, queryset):
        gym = self.get_gym(request)
        if gym:
            queryset = queryset.filter(gym=gym)
        queryset.update(status=Order.Status.CANCELLED)


# ── StaffDevice — gym-scoped ──────────────────────────────────────────────────

@admin.register(StaffDevice)
class StaffDeviceAdmin(GymScopedShopAdmin):
    list_display  = ['user', 'device_name', 'active', 'last_seen']
    list_filter   = ['active']
    search_fields = ['device_name', 'fcm_token', 'user__username']
    ordering      = ['-last_seen']
    readonly_fields = ['fcm_token', 'last_seen']

    def has_add_permission(self, request):
        # Devices are registered by the app — never created manually
        return False

    actions = ['deactivate_selected', 'activate_selected']

    @admin.action(description='Deactivate selected devices')
    def deactivate_selected(self, request, queryset):
        # Scope action to current gym — prevents staff from
        # deactivating another gym's devices via action on a crafted queryset
        gym = self.get_gym(request)
        if gym:
            queryset = queryset.filter(gym=gym)
        queryset.update(active=False)

    @admin.action(description='Activate selected devices')
    def activate_selected(self, request, queryset):
        gym = self.get_gym(request)
        if gym:
            queryset = queryset.filter(gym=gym)
        queryset.update(active=True)
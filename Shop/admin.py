# Shop/admin.py

from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import (
    GlobalProduct, GlobalProductFlavor,
    GymProduct, GymProductFlavor,
    Order, StaffDevice,
)
from .models import GymInventoryMovement
from django import forms as django_forms

# ── Base admin for gym-scoped Shop models ─────────────────────────────────────
class GymProductFlavorAdminForm(django_forms.ModelForm):
    class Meta:
        model = GymProductFlavor
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        selling_price  = cleaned.get('selling_price')
        discount_price = cleaned.get('discount_price')
        if (
            discount_price is not None
            and selling_price is not None
            and discount_price > selling_price
        ):
            self.add_error('discount_price', "Cannot be greater than selling price.")
        return cleaned
class GymScopedShopAdmin(admin.ModelAdmin):
    """
    Base for models with a gym FK (GymProduct, Order, StaffDevice).
    Superuser sees all gyms. Gym staff sees only their gym.
    """
    list_per_page = 50
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


# ── GlobalProduct — platform-global, superuser reviews approval ──────────────

class GlobalProductFlavorInline(admin.TabularInline):
    model = GlobalProductFlavor
    extra = 1
    fields = ['flavor_name', 'weight', 'image']


@admin.register(GlobalProduct)
class GlobalProductAdmin(admin.ModelAdmin):
    list_display   = ['name','category', 'approval_status', 'active', 'created_by']
    list_filter    = ['approval_status', 'active', 'brand', 'category']
    search_fields  = ['name', 'brand', 'category', 'slug']
    ordering       = ['-created_at']
    readonly_fields = ['slug', 'created_by', 'created_at', 'updated_at']
    inlines        = [GlobalProductFlavorInline]
    actions        = ['approve_products', 'reject_products']
    list_per_page = 50

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        if obj.approval_status == GlobalProduct.Approval.APPROVED and not obj.approved_by_id:
            obj.approved_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description='Approve selected products')
    def approve_products(self, request, queryset):
        for product in queryset:
            product.approval_status = GlobalProduct.Approval.APPROVED
            product.approved_by = request.user
            product.save(update_fields=['approval_status', 'approved_by'])

    @admin.action(description='Reject selected products')
    def reject_products(self, request, queryset):
        queryset.update(approval_status=GlobalProduct.Approval.REJECTED)

    def has_add_permission(self, request):
        return request.user.is_superuser or request.user.is_staff

    def has_change_permission(self, request, obj=None):
        # Any gym staff can propose edits via "Create New Product";
        # only superuser can edit brand/name/category/flavors on approved items
        if request.user.is_superuser:
            return True
        if obj is None:
            return True
        return obj.approval_status == GlobalProduct.Approval.PENDING

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(GlobalProductFlavor)
class GlobalProductFlavorAdmin(admin.ModelAdmin):
    list_display  = ['global_product', 'flavor_name', 'weight']
    list_filter   = ['global_product']
    search_fields = ['flavor_name', 'global_product__name']
    ordering      = ['global_product__name', 'flavor_name']
    list_per_page = 50
    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


# ── GymProduct — gym-scoped import record ─────────────────────────────────────

class GymProductFlavorInline(admin.TabularInline):
    model = GymProductFlavor
    form = GymProductFlavorAdminForm
    extra = 0
    fields = [
        'global_flavor', 'selling_price', 'discount_price', 'cost_price',
        'stock', 'sku', 'minimum_stock', 'active',
    ]
    readonly_fields = ['stock']

@admin.register(GymProduct)
class GymProductAdmin(GymScopedShopAdmin):
    list_display  = ['global_product', 'gym', 'active', 'is_visible', 'created_at']
    list_select_related = (
    "gym",
    "global_product",
    )
    list_filter   = ['active', 'is_visible']
    search_fields = ['global_product__name', 'global_product__brand',"gym__gym_name",]
    ordering      = ['display_order', '-created_at']
    inlines       = [GymProductFlavorInline]

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser and 'global_product' not in readonly:
            readonly.append('global_product')
        return readonly


@admin.register(GymProductFlavor)
class GymProductFlavorAdmin(GymScopedShopAdmin):
    form = GymProductFlavorAdminForm
    list_display  = ['gym_product', 'gym_column', 'global_flavor', 'discount_price', 'stock', 'active']
    list_select_related = (
        "gym_product",
        "gym_product__gym",
        "global_flavor",
    )
    list_filter   = ['active']
    search_fields = ['gym_product__global_product__name', 'sku']
    ordering      = ['gym_product']

    @admin.display(description='Gym')
    def gym_column(self, obj):
        return obj.gym_product.gym

    def get_list_display(self, request):
        return self.list_display

    def get_readonly_fields(self, request, obj=None):
        # CHANGED — override instead of inheriting the parent's auto-append,
        # since GymScopedShopAdmin.get_readonly_fields() blindly appends
        # 'gym', which doesn't exist as a field on this model.
        return list(super(GymScopedShopAdmin, self).get_readonly_fields(request, obj))

    def get_fields(self, request, obj=None):
        # CHANGED — same reasoning: parent's get_fields() tries to remove
        # 'gym' from the field list, which is a no-op-safe check normally,
        # but skip it entirely here for clarity/correctness.
        return list(super(GymScopedShopAdmin, self).get_fields(request, obj))

    def get_gym(self, request):
        return getattr(request, 'gym', None)

    def get_queryset(self, request):
        qs = super(GymScopedShopAdmin, self).get_queryset(request)
        if request.user.is_superuser:
            return qs
        gym = self.get_gym(request)
        if not gym:
            return qs.none()
        return qs.filter(gym_product__gym=gym)

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return True
        gym = self.get_gym(request)
        if gym and obj.gym_product.gym_id != gym.pk:
            return False
        return True

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)

# ── Order — gym-scoped ────────────────────────────────────────────────────────

@admin.register(Order)
class OrderAdmin(GymScopedShopAdmin):
    list_display  = [
        'id', 'user', 'gym_flavor', 'quantity', 'total_price', 'status',
    ]
    list_select_related = (
        "user",
        "gym",
        "gym_product",
        "gym_flavor",
    )
    list_filter   = ['status']
    search_fields = ['user__username', 'gym_product__global_product__name']
    ordering      = ['-ordered_at']
    readonly_fields = ['ordered_at', 'updated_at', 'total_price', 'unit_price']

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            for field in ['user', 'gym_product', 'gym_flavor', 'quantity']:
                if field not in readonly:
                    readonly.append(field)
        return readonly

    actions = ['mark_confirmed', 'mark_delivered', 'mark_cancelled']

    @admin.action(description='Mark selected orders as Confirmed')
    def mark_confirmed(self, request, queryset):
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
@admin.register(GymInventoryMovement)
class GymInventoryMovementAdmin(admin.ModelAdmin):
    list_display  = ['gym_product_flavor', 'movement_type','stock_after', 'created_by']
    list_select_related = (
        "gym_product_flavor",
        "gym_product_flavor__gym_product",
        "created_by",
    )
    list_filter   = ['movement_type']
    search_fields = ['gym_product_flavor__gym_product__global_product__name', 'reason']
    ordering      = ['-created_at']
    readonly_fields = [f.name for f in GymInventoryMovement._meta.fields]  # fully immutable in admin
    list_per_page = 50

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        gym = getattr(request, 'gym', None)
        if not gym:
            return qs.none()
        return qs.filter(gym_product_flavor__gym_product__gym=gym)

    def has_add_permission(self, request):
        return False  # movements are only created via StockMovementService

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

# ── StaffDevice — unchanged ────────────────────────────────────────────────────

@admin.register(StaffDevice)
class StaffDeviceAdmin(GymScopedShopAdmin):
    list_display  = ['user', 'device_name', 'active', 'last_seen']
    list_filter   = ['active']
    search_fields = ['device_name', 'fcm_token', 'user__username']
    ordering      = ['-last_seen']
    list_select_related = (
        "gym",
        "user",
    )
    readonly_fields = ['fcm_token', 'last_seen']

    def has_add_permission(self, request):
        return False

    actions = ['deactivate_selected', 'activate_selected']

    @admin.action(description='Deactivate selected devices')
    def deactivate_selected(self, request, queryset):
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
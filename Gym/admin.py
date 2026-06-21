# Gym/admin.py

from django.contrib import admin
from django.utils.html import format_html
from cloudinary.utils import cloudinary_url
from .models import Gym, SubscriptionPlan, StaffProfile


# ──────────────────────────────────────────────────────────────────────────────
# SubscriptionPlan
# ──────────────────────────────────────────────────────────────────────────────
@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display  = ['name', 'price_monthly', 'member_limit', 'trainer_limit']
    search_fields = ['name']
    ordering      = ['price_monthly']


# ──────────────────────────────────────────────────────────────────────────────
# Gym
# ──────────────────────────────────────────────────────────────────────────────
@admin.register(Gym)
class GymAdmin(admin.ModelAdmin):
    list_display    = ('gym_name', 'gym_code', 'logo_preview', 'active', 'plan')
    list_filter      = ['active', 'plan']
    search_fields    = ['gym_name', 'gym_code', 'owner__username']
    readonly_fields  = ['id', 'logo_preview_large', 'days_until_expiry', 'created_at', 'updated_at']
    ordering         = ['gym_name']
    prepopulated_fields = {'gym_code': ('gym_name',)}
    fieldsets = (
        ('Identity', {
            'fields': ('id', 'gym_name', 'gym_code', 'owner'),
        }),
        ('Subscription', {
            'fields': ('plan', 'active', 'subscription_start', 'subscription_end',
                       'days_until_expiry', 'member_limit', 'trainer_limit'),
        }),
        ('White-label', {
            'fields': ('logo', 'logo_preview_large', 'theme_color', 'contact_email',
                       'contact_phone', 'whatsapp_number', 'address', 'city', 'website',
                       'receipt_footer'),
            'classes': ('collapse',),
        }),
        ('Geo-fence', {
            'fields': ('latitude', 'longitude', 'radius_meters'),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(boolean=True, description='Subscription Active')
    def subscription_status(self, obj):
        return obj.is_subscription_active

    # ── Logo preview helpers ────────────────────────────────────────────
    def _logo_thumb_url(self, obj, size):
        if not obj or not obj.logo:
            return None
        try:
            public_id = obj.logo.public_id if hasattr(obj.logo, "public_id") else str(obj.logo)
            if not public_id:
                return None
            url, _ = cloudinary_url(
                public_id, width=size, height=size,
                crop="fill", gravity="center",
                fetch_format="auto", quality="auto", secure=True,
            )
            return url
        except Exception:
            return None

    @admin.display(description="Logo")
    def logo_preview(self, obj):
        url = self._logo_thumb_url(obj, size=60)
        if url:
            return format_html(
                '<img src="{}" width="60" height="60" style="object-fit:cover;border-radius:6px;" />',
                url,
            )
        return "-"

    @admin.display(description="Current Logo")
    def logo_preview_large(self, obj):
        url = self._logo_thumb_url(obj, size=200)
        if url:
            return format_html(
                '<img src="{}" width="200" height="200" '
                'style="object-fit:contain;background:#111;border-radius:8px;padding:8px;" />',
                url,
            )
        return "No logo uploaded yet."


# ──────────────────────────────────────────────────────────────────────────────
# StaffProfile
# ──────────────────────────────────────────────────────────────────────────────
@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display  = ['user', 'gym', 'role', 'active']
    list_filter   = ['role', 'active', 'gym']
    search_fields = ['user__username', 'gym__gym_name', 'gym__gym_code']
    ordering      = ['gym', 'role']
    autocomplete_fields = ['user', 'gym']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        try:
            return qs.filter(gym=request.user.staff_profile.gym)
        except StaffProfile.DoesNotExist:
            return qs.none()
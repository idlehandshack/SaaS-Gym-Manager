# Gym/admin.py

from django.contrib import admin
from django.utils.html import format_html
from cloudinary.utils import cloudinary_url
from .models import Gym, SubscriptionPlan, StaffProfile , GymGSTProfile


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
    list_display    = ('gym_name', 'gym_code', 'logo_preview', 'favicon_preview', 'active', 'plan',
                       'enable_store', 'enable_attendance', 'enable_trainers')
    list_editable   = ('enable_store', 'enable_attendance', 'enable_trainers')
    list_filter     = ['active', 'plan']
    search_fields   = ['gym_name', 'gym_code', 'owner__username']
    readonly_fields = ['id', 'logo_preview_large', 'favicon_preview_large',
                    'splash_logo_preview_large', 'days_until_expiry',
                    'created_at', 'updated_at']
    ordering        = ['gym_name']
    prepopulated_fields = {'gym_code': ('gym_name',)}
    fieldsets = (
        ('Identity', {
            'fields': ('id', 'gym_name', 'gym_code', 'owner'),
        }),
        ('Subscription', {
            'fields': ('plan', 'active', 'subscription_start', 'subscription_end',
                       'days_until_expiry', 'member_limit', 'trainer_limit'),
        }),
        ('Module Flags', {
            'fields': (
                'enable_store',
                'enable_attendance',
                'enable_face_recognition',
                'enable_trainers',
            ),
            'description': (
                'Toggle individual modules on/off for this gym. '
                'Disabling a module blocks all URLs and hides UI for that feature.'
            ),
        }),
        ('White-label', {
            'fields': ('app_name', 'app_short_name',
                    'logo', 'logo_preview_large',
                    'favicon', 'favicon_preview_large',
                    'splash_logo', 'splash_logo_preview_large',
                    'theme_color',
                    'contact_email', 'contact_phone',
                    'whatsapp_number', 'address', 'city', 'website',
                    'app_download_url'),
            'classes': ('collapse',),
        }),
        ('Geo-fence', {
            'fields': ('latitude', 'longitude', 'radius_meters','map'),
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

    # ── Shared Cloudinary thumb helper ──────────────────────────────────
    def _thumb_url(self, field_value, size):
        if not field_value:
            return None
        try:
            public_id = field_value.public_id if hasattr(field_value, 'public_id') else str(field_value)
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

    # ── Logo previews ───────────────────────────────────────────────────
    @admin.display(description="Logo")
    def logo_preview(self, obj):
        url = self._thumb_url(obj.logo, 40)
        if url:
            return format_html(
                '<img src="{}" width="40" height="40" '
                'style="object-fit:cover;border-radius:6px;" />',
                url,
            )
        return "-"

    @admin.display(description="Current Logo")
    def logo_preview_large(self, obj):
        url = self._thumb_url(obj.logo, 200)
        if url:
            return format_html(
                '<img src="{}" width="200" height="200" '
                'style="object-fit:contain;background:#111;border-radius:8px;padding:8px;" />',
                url,
            )
        return "No logo uploaded yet."

    # ── Favicon previews ────────────────────────────────────────────────
    @admin.display(description="Favicon")
    def favicon_preview(self, obj):
        url = self._thumb_url(obj.favicon, 32)
        if url:
            return format_html(
                '<img src="{}" width="32" height="32" '
                'style="object-fit:cover;border-radius:4px;" />',
                url,
            )
        return "-"

    @admin.display(description="Current Favicon")
    def favicon_preview_large(self, obj):
        url = self._thumb_url(obj.favicon, 128)
        if url:
            return format_html(
                '<img src="{}" width="128" height="128" '
                'style="object-fit:contain;background:#111;border-radius:8px;padding:8px;" />',
                url,
            )
        return "No favicon uploaded yet."
    @admin.display(description="Current Splash Logo")
    def splash_logo_preview_large(self, obj):
        url = self._thumb_url(obj.splash_logo, 200)
        if url:
            return format_html(
                '<img src="{}" width="200" height="200" '
                'style="object-fit:contain;background:#111;border-radius:8px;padding:8px;" />',
                url,
            )
        return "No splash image uploaded yet."

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
        

@admin.register(GymGSTProfile)
class GymGSTProfileAdmin(admin.ModelAdmin):
    list_display = (
        'gym',
        'legal_business_name',
        'is_gst_registered',
        'gstin',
        'state',
        'composition_scheme',
    )
    list_filter = (
        'is_gst_registered',
        'composition_scheme',
        'state',
    )
    search_fields = (
        'legal_business_name',
        'gstin',
        'gym__gym_name',
        'gym__gym_code',
    )
    autocomplete_fields = ('gym',)
    fieldsets = (
        ('Gym', {
            'fields': ('gym',)
        }),
        ('Business Details', {
            'fields': (
                'legal_business_name',
                'is_gst_registered',
                'composition_scheme',
                'gstin',
            )
        }),
        ('Address', {
            'fields': (
                'address_line1',
                'address_line2',
                'city',
                'state',
                'state_code',
                'pincode',
            )
        }),
        ('Invoice Settings', {
            'fields': (
                'invoice_series_prefix',
                'default_sac_membership',
            )
        }),
        ('Signature', {
            'fields': ('signature_image',)
        }),
    )
# 2 Gym/admin.py

from django.contrib import admin
from django.utils.html import format_html
from cloudinary.utils import cloudinary_url
from .models import Gym, SubscriptionPlan, StaffProfile , GymGSTProfile ,PlatformSubscriptionPayment ,PlatformSettings ,StaffPermission ,OrphanUserDeletionLog ,EquipmentBrand ,Service ,GymWhatsAppSettings ,WhatsAppMessageLog,GymAICredit, AICreditTransaction
from Gym.ai_credit_service import admin_adjust_credits
from django import forms

@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'image_preview', 'sort_order', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    ordering = ('sort_order', 'name')
    list_per_page = 50
    list_editable = ('sort_order', 'is_active')
    actions = ['activate_services', 'deactivate_services']
 
    def image_preview(self, obj):
        from django.utils.html import format_html
        if obj.image:
            return format_html('<img src="{}" style="height:40px;border-radius:4px;">', obj.image.url)
        return "—"
    image_preview.short_description = "Preview"
 
    @admin.action(description='Mark selected services as active')
    def activate_services(self, request, queryset):
        queryset.update(is_active=True)
 
    @admin.action(description='Mark selected services as inactive')
    def deactivate_services(self, request, queryset):
        queryset.update(is_active=False)

@admin.register(StaffPermission)
class StaffPermissionAdmin(admin.ModelAdmin):
    list_display = ("staff_profile", "updated_at", "updated_by")
    search_fields = ("staff_profile__user__username", "staff_profile__gym__gym_name")
    autocomplete_fields = ("staff_profile", "updated_by")
    list_filter = ("staff_profile__role", "staff_profile__gym")

@admin.register(OrphanUserDeletionLog)
class OrphanUserDeletionLogAdmin(admin.ModelAdmin):
    list_display = ('username', 'deleted_user_id', 'deleted_by', 'deleted_at')
    list_filter = ('deleted_at',)
    search_fields = ('username', 'email')
    readonly_fields = [f.name for f in OrphanUserDeletionLog._meta.fields]

    def has_add_permission(self, request):
        return False  # audit log — created only via the delete flow
    def has_change_permission(self, request, obj=None):
        return False

# ──────────────────────────────────────────────────────────────────────────────
# SubscriptionPlan
# ──────────────────────────────────────────────────────────────────────────────
@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display  = ['name', 'price_monthly', 'member_limit', 'trainer_limit']
    search_fields = ['name']
    ordering      = ['price_monthly']

@admin.register(PlatformSubscriptionPayment)
class PlatformSubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ("gym", "amount", "paid_on", "period_start", "period_end")
    list_filter = ("paid_on",)
    search_fields = ("gym__gym_name",)
# ──────────────────────────────────────────────────────────────────────────────
# Gym
# ──────────────────────────────────────────────────────────────────────────────
@admin.register(Gym)
class GymAdmin(admin.ModelAdmin):
    list_display    = ('gym_name', 'gym_code', 'logo_preview', 'favicon_preview', 'active', 'plan', 'theme','enable_store', 'enable_attendance','enable_geo_attendance', 'enable_trainers', 'upi_enabled')
    list_editable   = ('enable_store', 'enable_attendance', 'enable_geo_attendance', 'enable_trainers')
    list_filter     = ['active', 'plan', 'theme']
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
            'fields': ('plan', 'active', 'subscription_start', 'subscription_end','show_subscription_payment',
                       'days_until_expiry', 'member_limit', 'trainer_limit'),
        }),
        ('Module Flags', {
            'fields': (
                'enable_store',
                'enable_attendance',
                'enable_geo_attendance',
                'enable_face_recognition',
                'enable_trainers',
            ),
            'description': (
                'Toggle individual modules on/off for this gym. '
                'Disabling a module blocks all URLs and hides UI for that feature. '
                'enable_geo_attendance is separate from enable_attendance — a gym can '
                'keep Attendance visible (e.g. for face check-in) while GPS is off.'
            ),
        }),
        ('White-label', {
            'fields': ('app_name', 'app_short_name',
                    'logo', 'logo_preview_large',
                    'favicon', 'favicon_preview_large',
                    'splash_logo', 'splash_logo_preview_large',
                    'theme_color',
                    'theme', 
                    'contact_email', 'contact_phone',
                    'whatsapp_number', 'address', 'city',
                    'app_download_url'),
            'classes': ('collapse',),
        }),
        ('UPI Payment Settings', {
            'fields': (
                'upi_enabled',
                'upi_id',
                'upi_display_name',
                'upi_payment_note',
            ),
            'description': (
                'Lets members pay this gym directly via UPI deep link from their profile page. '
                'This is NOT a payment gateway — the gym owner must still verify and record the '
                'payment manually in Payment Management. UPI ID and Display Name are required '
                'when UPI is enabled.'
            ),
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
    @admin.display(boolean=True, description='UPI Enabled')
    def upi_enabled_display(self, obj):
        return obj.upi_enabled
    
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
        gym_ids = request.user.staff_profiles.filter(active=True).values_list('gym_id', flat=True)
        return qs.filter(gym_id__in=gym_ids)
        

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

@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    list_display = ('upi_id', 'upi_display_name')

    def has_add_permission(self, request):
        return not PlatformSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

@admin.register(EquipmentBrand)
class EquipmentBrandAdmin(admin.ModelAdmin):
    list_display = ('name','is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    ordering = ('name',)
    list_per_page = 50
    list_editable = ('is_active',)
    actions = ['activate_brands', 'deactivate_brands']

    @admin.action(description='Mark selected brands as active')
    def activate_brands(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description='Mark selected brands as inactive')
    def deactivate_brands(self, request, queryset):
        queryset.update(is_active=False)

@admin.register(GymWhatsAppSettings)
class GymWhatsAppSettingsAdmin(admin.ModelAdmin):
    """
    Read-mostly in Django admin — credentials are managed exclusively
    through the owner-only dashboard (Gym.whatsapp_views.whatsapp_settings),
    never re-typed here. The three secret fields
    (permanent_access_token, webhook_verify_token, webhook_secret) are
    EncryptedTextFields that decrypt transparently on read, so a normal
    admin input would print the live plaintext secret on screen — instead
    they're masked, read-only display fields.
    """
    list_display = (
        'gym', 'status', 'enabled', 'phone_number', 'masked_business_account_id',
        'verified_at', 'updated_at',
    )
    list_filter = ('status', 'enabled')
    search_fields = ('gym__gym_name', 'gym__gym_code', 'phone_number', 'business_name')
    autocomplete_fields = ('gym',)
 
    readonly_fields = (
        'status', 'verified_at', 'last_error', 'created_at', 'updated_at',
        'masked_permanent_access_token', 'masked_webhook_verify_token', 'masked_webhook_secret',
    )
 
    fieldsets = (
        ('Gym', {'fields': ('gym', 'enabled')}),
        ('Business Identity', {
            'fields': ('business_name', 'phone_number', 'phone_number_id', 'business_account_id'),
        }),
        ('Credentials (masked — managed via the owner dashboard, not here)', {
            'fields': (
                'masked_permanent_access_token',
                'masked_webhook_verify_token',
                'masked_webhook_secret',
            ),
        }),
        ('Connection Health', {
            'fields': ('status', 'verified_at', 'last_error'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
 
    @admin.display(description='Business Account ID')
    def masked_business_account_id(self, obj):
        val = obj.business_account_id or ''
        if len(val) <= 4:
            return val
        return f"{'*' * (len(val) - 4)}{val[-4:]}"
 
    def _masked_secret(self, value):
        if not value:
            return '— not set —'
        return format_html('{}', f"{'•' * 12} (set, hidden — edit via the owner dashboard)")
 
    @admin.display(description='Permanent Access Token')
    def masked_permanent_access_token(self, obj):
        return self._masked_secret(obj.permanent_access_token)
 
    @admin.display(description='Webhook Verify Token')
    def masked_webhook_verify_token(self, obj):
        return self._masked_secret(obj.webhook_verify_token)
 
    @admin.display(description='Webhook Secret')
    def masked_webhook_secret(self, obj):
        return self._masked_secret(obj.webhook_secret)
 
    def has_add_permission(self, request):
        # Rows are created via get_or_create() the first time an owner
        # opens the WhatsApp settings page — never manually in admin,
        # matching the has_add_permission=False idiom already used by
        # OrphanUserDeletionLogAdmin / PlatformSettingsAdmin above.
        return False
 
 
@admin.register(WhatsAppMessageLog)
class WhatsAppMessageLogAdmin(admin.ModelAdmin):
    """
    Pure audit trail — same read-only pattern as OrphanUserDeletionLogAdmin
    above: has_add_permission/has_change_permission both False,
    readonly_fields built from every model field via the same
    list-comprehension idiom.
    """
    list_display = (
        'created_at', 'gym', 'phone', 'message_type', 'template_name',
        'status', 'status_code', 'deduplication_key_short',
    )
    list_filter = ('status', 'message_type', 'gym')
    search_fields = ('phone', 'message_id', 'deduplication_key', 'gym__gym_name', 'gym__gym_code')
    date_hierarchy = 'created_at'
    autocomplete_fields = ('gym', 'member')
    readonly_fields = [f.name for f in WhatsAppMessageLog._meta.fields]
 
    @admin.display(description='Dedup Key')
    def deduplication_key_short(self, obj):
        key = obj.deduplication_key or ''
        return (key[:40] + '…') if len(key) > 40 else key
 
    def has_add_permission(self, request):
        return False  # audit log — created only via the sending flow
 
    def has_change_permission(self, request, obj=None):
        return False

class GymAICreditAdminForm(forms.ModelForm):
    """
    Adds two virtual (non-model) fields so a Super Admin can top-up or
    deduct credits straight from the wallet's admin page, with a mandatory
    reason. balance/total_used stay read-only — they only ever move
    through admin_adjust_credits() so the ledger can't drift.
    """
    adjustment_amount = forms.IntegerField(
        required=False, initial=0,
        help_text="Positive to add credits, negative to deduct. 0 = no change.",
    )
    adjustment_reason = forms.CharField(
        required=False, max_length=255,
        help_text="Required if Adjustment Amount is non-zero.",
    )

    class Meta:
        model = GymAICredit
        fields = ['gym']

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get('adjustment_amount') or 0
        reason = (cleaned.get('adjustment_reason') or '').strip()
        if amount and not reason:
            raise forms.ValidationError("A reason is required when adjusting credits.")
        return cleaned


@admin.register(GymAICredit)
class GymAICreditAdmin(admin.ModelAdmin):
    form = GymAICreditAdminForm
    list_display = ('gym', 'balance', 'total_used', 'updated_at')
    search_fields = ('gym__gym_name', 'gym__gym_code')
    readonly_fields = ('balance', 'total_used', 'created_at', 'updated_at')
    autocomplete_fields = ('gym',)
    fieldsets = (
        ('Gym', {'fields': ('gym',)}),
        ('Current Wallet (read-only — use Manual Adjustment to change it)', {
            'fields': ('balance', 'total_used', 'created_at', 'updated_at'),
        }),
        ('Manual Adjustment', {
            'fields': ('adjustment_amount', 'adjustment_reason'),
            'description': 'Add or deduct AI credits for this gym. Every '
                            'adjustment is recorded in AI Credit Transactions.',
        }),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        amount = form.cleaned_data.get('adjustment_amount') or 0
        reason = (form.cleaned_data.get('adjustment_reason') or '').strip()
        if amount:
            admin_adjust_credits(obj.gym, delta=amount, reason=reason, created_by=request.user)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # 1:1 with a gym — deleting one is never a normal admin action


@admin.register(AICreditTransaction)
class AICreditTransactionAdmin(admin.ModelAdmin):
    """Pure audit trail — same read-only pattern as WhatsAppMessageLogAdmin."""
    list_display = ('created_at', 'gym', 'credits', 'balance_after', 'reason', 'created_by')
    list_filter = ('gym',)
    search_fields = ('gym__gym_name', 'gym__gym_code', 'reason')
    date_hierarchy = 'created_at'
    autocomplete_fields = ('gym', 'created_by')
    readonly_fields = [f.name for f in AICreditTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
# Gym/admin.py

from django.contrib import admin
from django.utils.html import format_html
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
    list_display  = [
        'gym_name', 'gym_code', 'owner', 'plan',
        'subscription_status', 'subscription_end', 'active',
    ]
    list_filter   = ['active', 'plan']
    search_fields = ['gym_name', 'gym_code', 'owner__username']
    readonly_fields = ['id', 'created_at', 'updated_at', 'days_until_expiry']
    ordering      = ['gym_name']
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
            'fields': ('logo', 'theme_color', 'contact_email', 'contact_phone',
                       'whatsapp_number', 'address', 'city', 'website',
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
        # Superuser sees all gyms' staff
        if request.user.is_superuser:
            return qs
        # Gym owner only sees staff at their own gym
        try:
            return qs.filter(gym=request.user.staff_profile.gym)
        except StaffProfile.DoesNotExist:
            return qs.none()
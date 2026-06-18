# notification/admin.py

from django.contrib import admin
from .models import WebPushSubscription


@admin.register(WebPushSubscription)
class WebPushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'active', 'last_used', 'created_at', 'short_endpoint')
    list_filter  = ('active',)
    readonly_fields = ('user', 'endpoint', 'p256dh', 'auth', 'created_at')

    # ── Remove list_filter on user — shows ALL users across all gyms ─────
    # Replaced with gym-scoped queryset filtering below

    def short_endpoint(self, obj):
        return obj.endpoint[:60] + '...'
    short_endpoint.short_description = 'Endpoint'

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        # Superuser sees all subscriptions across all gyms
        if request.user.is_superuser:
            return qs

        # Gym owner / staff — scope to users who belong to their gym
        # WebPushSubscription has no gym FK directly, so we resolve via:
        #   user → StaffProfile → gym   (for staff subscriptions)
        gym = getattr(request, 'gym', None)
        if not gym:
            return qs.none()

        # Get all user IDs that belong to this gym
        # (staff members of this gym)
        from Gym.models import StaffProfile
        gym_user_ids = StaffProfile.objects.filter(
            gym=gym, active=True
        ).values_list('user_id', flat=True)

        return qs.filter(user_id__in=gym_user_ids)

    def has_change_permission(self, request, obj=None):
        # Nobody should edit push subscriptions manually
        # They are created/deleted by the browser JS automatically
        return False

    def has_add_permission(self, request):
        # Subscriptions are created by the browser — never manually
        return False

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return True
        # Gym staff can only delete subscriptions belonging to their gym's users
        if request.user.is_superuser:
            return True
        gym = getattr(request, 'gym', None)
        if not gym:
            return False
        from Gym.models import StaffProfile
        return StaffProfile.objects.filter(
            gym=gym, user=obj.user, active=True
        ).exists()
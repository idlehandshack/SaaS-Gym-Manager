from django.contrib import admin

from .models import Announcement, AnnouncementRead


class AnnouncementReadInline(admin.TabularInline):
    model = AnnouncementRead
    extra = 0
    readonly_fields = ('user', 'read_at', 'dismissed', 'dismissed_at', 'device_type', 'created_at')
    can_delete = False
    max_num = 0
    show_change_link = False


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'gym', 'announcement_type', 'priority', 'target_audience',
        'is_active', 'is_live_display', 'publish_at', 'expires_at',
        'push_sent_count', 'created_by',
    )
    list_filter = ('gym', 'announcement_type', 'priority', 'target_audience', 'is_active')
    search_fields = ('title', 'description', 'gym__gym_name', 'gym__gym_code')
    # NOTE: autocomplete_fields requires the referenced model to have its
    # own registered ModelAdmin with search_fields (admin.E039 otherwise).
    # Gym and User already have admins registered elsewhere in the project;
    # MembershipPlan/Trainer (AuthFit) may not, so those use plain selects
    # via raw_id_fields instead to avoid a hard dependency on that admin
    # registration existing.
    autocomplete_fields = ('gym', 'created_by')
    raw_id_fields = ('target_plan', 'target_trainer')
    filter_horizontal = ('target_members',)
    readonly_fields = ('push_sent_at', 'push_sent_count', 'view_count', 'created_at', 'updated_at')
    date_hierarchy = 'publish_at'
    inlines = [AnnouncementReadInline]

    fieldsets = (
        ('Ownership', {'fields': ('gym', 'created_by')}),
        ('Content', {'fields': ('title', 'description', 'announcement_type', 'priority', 'image', 'attachment', 'external_link')}),
        ('Scheduling', {'fields': ('publish_at', 'expires_at', 'is_active')}),
        ('Display Channels', {'fields': (
            'show_popup', 'show_banner', 'show_web', 'show_mobile',
            'send_push', 'require_read', 'pin_home',
        )}),
        ('Targeting', {'fields': ('target_audience', 'target_plan', 'target_trainer', 'target_members')}),
        ('Delivery Stats', {'fields': ('push_sent_at', 'push_sent_count', 'view_count', 'created_at', 'updated_at')}),
    )

    @admin.display(boolean=True, description='Live now')
    def is_live_display(self, obj):
        return obj.is_live

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('gym', 'created_by')
        if request.user.is_superuser:
            return qs
        profile = getattr(request.user, 'staff_profile', None)
        if profile and profile.role == 'gym_owner':
            return qs.filter(gym=profile.gym)
        return qs.none()

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        profile = getattr(request.user, 'staff_profile', None)
        return bool(profile and profile.role == 'gym_owner')

    def has_change_permission(self, request, obj=None):
        return self.has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_add_permission(request) 


@admin.register(AnnouncementRead)
class AnnouncementReadAdmin(admin.ModelAdmin):
    list_display = ('announcement', 'user', 'read_at', 'dismissed', 'device_type')
    list_filter = ('dismissed', 'device_type')
    search_fields = ('announcement__title', 'user__username')
    autocomplete_fields = ('announcement', 'user')

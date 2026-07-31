from django.contrib import admin

from .models import (
    Communication,
    CommunicationAudience,
    CommunicationCampaign,
    CommunicationDeliveryLog,
    CommunicationSponsor,
)


class CommunicationAudienceInline(admin.StackedInline):
    model = CommunicationAudience
    extra = 0
    max_num = 1
    filter_horizontal = ('gyms', 'plans', 'subscription_plans', 'specific_members', 'specific_staff')


@admin.register(Communication)
class CommunicationAdmin(admin.ModelAdmin):
    list_display = ('title', 'type', 'priority', 'status', 'publish_at', 'expires_at',
                     'dispatch_success_count', 'dispatch_failure_count', 'created_by')
    list_filter = ('type', 'priority', 'status', 'is_active')
    search_fields = ('title', 'description')
    date_hierarchy = 'publish_at'
    inlines = [CommunicationAudienceInline]
    readonly_fields = ('dispatched_at', 'dispatch_success_count', 'dispatch_failure_count',
                        'total_impressions', 'created_at', 'updated_at')


@admin.register(CommunicationDeliveryLog)
class CommunicationDeliveryLogAdmin(admin.ModelAdmin):
    list_display = ('communication', 'gym', 'recipient', 'channel', 'status', 'created_at')
    list_filter = ('channel', 'status')
    search_fields = ('communication__title', 'recipient__username')
    date_hierarchy = 'created_at'
    readonly_fields = [f.name for f in CommunicationDeliveryLog._meta.fields]

    def has_add_permission(self, request):
        return False  # audit-style — created only by CommunicationDispatcher


@admin.register(CommunicationSponsor)
class CommunicationSponsorAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_person', 'email', 'phone', 'status')
    list_filter = ('status',)
    search_fields = ('name', 'contact_person', 'email')


@admin.register(CommunicationCampaign)
class CommunicationCampaignAdmin(admin.ModelAdmin):
    list_display = ('name', 'sponsor', 'budget', 'start_date', 'end_date', 'status')
    list_filter = ('status', 'sponsor')
    search_fields = ('name',)

from django.contrib import admin
from .models import MemberMessage


@admin.register(MemberMessage)
class MemberMessageAdmin(admin.ModelAdmin):
    list_display = ('title', 'member', 'gym', 'priority', 'is_read', 'created_at', 'deleted_at')
    list_filter = ('priority', 'is_read', 'gym')
    search_fields = ('title', 'message', 'member__username', 'member__first_name', 'member__last_name')
    readonly_fields = ('created_at', 'updated_at')

    def get_queryset(self, request):
        # Show soft-deleted rows too, for audit purposes.
        return MemberMessage.all_objects.select_related('gym', 'member', 'created_by')

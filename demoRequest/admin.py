from django.contrib import admin
from .models import DemoRequest


@admin.register(DemoRequest)
class DemoRequestAdmin(admin.ModelAdmin):
    list_display = (
        "gym_name", "owner_name", "phone_number", "city",
        "created_at", "contacted", "email_sent", "push_sent",
    )
    list_filter = ("contacted", "preferred_language", "gym_size", "source", "created_at")
    search_fields = ("gym_name", "owner_name", "phone_number", "email")
    ordering = ("-created_at",)
    readonly_fields = (
        "gym_name", "owner_name", "phone_number", "email", "city",
        "gym_size", "preferred_language", "message", "source",
        "ip_address", "user_agent", "created_at", "updated_at",
        "email_sent", "push_sent",
    )
    fields = (
        ("gym_name", "owner_name"),
        ("phone_number", "email", "city"),
        ("gym_size", "preferred_language", "source"),
        "message",
        ("ip_address", "user_agent"),
        ("created_at", "updated_at"),
        ("email_sent", "push_sent"),
        "contacted",
        "contacted_at",
        "notes",
    )

    def has_add_permission(self, request):
        return False
# reviews/admin.py

from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone

from .models import Review, ReviewGenerationLog


class OverallRatingFilter(admin.SimpleListFilter):
    title = 'overall rating'
    parameter_name = 'overall_rating_bucket'

    def lookups(self, request, model_admin):
        return [('5', '5 stars'), ('4', '4+ stars'), ('3', '3 or below')]

    def queryset(self, request, queryset):
        if self.value() == '5':
            return queryset.filter(overall_rating=5)
        if self.value() == '4':
            return queryset.filter(overall_rating__gte=4)
        if self.value() == '3':
            return queryset.filter(overall_rating__lte=3)
        return queryset


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = (
        'gym', 'owner', 'overall_rating', 'overall_average',
        'is_published', 'is_hidden', 'version', 'approved_at', 'updated_at',
    )
    list_filter = ('is_published', 'is_hidden', OverallRatingFilter)
    search_fields = ('gym__gym_name', 'gym__gym_code', 'owner__username', 'owner__email')
    readonly_fields = (
        'overall_average', 'version', 'created_at', 'updated_at', 'approved_at',
        'draft_vs_final',
    )
    actions = ['publish_reviews', 'unpublish_reviews', 'hide_reviews', 'unhide_reviews']

    fieldsets = (
        ('Tenant', {'fields': ('gym', 'owner')}),
        ('Ratings', {'fields': (
            'overall_rating', 'ease_of_use_rating', 'daily_work_rating',
            'member_management_rating', 'attendance_rating', 'billing_rating',
            'pending_payment_rating', 'analytics_rating', 'support_rating',
            'value_rating', 'recommendation_rating', 'overall_average',
        )}),
        ('Content', {'fields': ('draft_vs_final', 'ai_generated_review', 'final_review')}),
        ('Status', {'fields': ('is_published', 'is_hidden', 'version', 'approved_at')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )

    def draft_vs_final(self, obj):
        if not obj.pk:
            return '—'
        return format_html(
            '<div style="display:flex;gap:16px;">'
            '<div style="flex:1;"><strong>AI Draft</strong><p style="white-space:pre-wrap;">{}</p></div>'
            '<div style="flex:1;"><strong>Final (Published)</strong><p style="white-space:pre-wrap;">{}</p></div>'
            '</div>',
            obj.ai_generated_review or '(no draft yet)',
            obj.final_review or '(no final version yet)',
        )
    draft_vs_final.short_description = 'AI Draft vs Final Version'

    @admin.action(description='Publish selected reviews')
    def publish_reviews(self, request, queryset):
        updated = queryset.update(is_published=True, approved_at=timezone.now())
        self.message_user(request, f"{updated} review(s) published.")

    @admin.action(description='Unpublish selected reviews')
    def unpublish_reviews(self, request, queryset):
        updated = queryset.update(is_published=False)
        self.message_user(request, f"{updated} review(s) unpublished.")

    @admin.action(description='Hide selected reviews from public site')
    def hide_reviews(self, request, queryset):
        updated = queryset.update(is_hidden=True)
        self.message_user(request, f"{updated} review(s) hidden.")

    @admin.action(description='Unhide selected reviews')
    def unhide_reviews(self, request, queryset):
        updated = queryset.update(is_hidden=False)
        self.message_user(request, f"{updated} review(s) unhidden.")


@admin.register(ReviewGenerationLog)
class ReviewGenerationLogAdmin(admin.ModelAdmin):
    list_display = ('gym', 'owner', 'success', 'created_at')
    list_filter = ('success',)
    search_fields = ('gym__gym_name', 'owner__username')
    readonly_fields = [f.name for f in ReviewGenerationLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # enforced at model level too — permanent audit trail

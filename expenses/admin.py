from django.contrib import admin
from .models import Expense,ExpenseTemplate


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('title', 'gym', 'category', 'amount', 'payment_method',
                     'expense_date', 'is_recurring', 'created_by')
    list_filter = ('category', 'payment_method', 'is_recurring', 'gym')
    search_fields = ('title', 'note')          # exact/indexed fields only — avoid icontains on gym FK
    date_hierarchy = 'expense_date'
    list_select_related = ('gym', 'created_by')   # avoid N+1 on list page
    show_full_result_count = False                # avoid COUNT(*) on large tables
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-expense_date',)

@admin.register(ExpenseTemplate)
class ExpenseTemplateAdmin(admin.ModelAdmin):
    list_display = ('title', 'gym', 'category', 'amount', 'is_active',
                     'start_date', 'next_run_date', 'end_date')
    list_filter = ('is_active', 'category', 'gym')
    search_fields = ('title', 'note')
    list_select_related = ('gym', 'created_by')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('title',)
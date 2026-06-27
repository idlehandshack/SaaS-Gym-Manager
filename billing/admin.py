from django.contrib import admin
from django.utils.html import format_html

from billing.models import Invoice, InvoiceLineItem, InvoiceCounter, Payment


class InvoiceLineItemInline(admin.TabularInline):
    model  = InvoiceLineItem
    extra  = 0
    fields = ('description', 'hsn_sac_code', 'quantity', 'unit_price',
              'taxable_value', 'gst_rate', 'cgst_amount', 'sgst_amount', 'igst_amount')
    readonly_fields = fields


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display  = ('invoice_number', 'invoice_date', 'gym', 'customer_name',
                     'invoice_type', 'grand_total', 'status', 'pdf_link')
    list_filter   = ('status', 'invoice_type', 'financial_year', 'gym')
    search_fields = ('invoice_number', 'customer_name', 'customer_gstin')
    readonly_fields = ('invoice_number', 'financial_year', 'created_at', 'updated_at', 'pdf_url')
    inlines       = [InvoiceLineItemInline]
    ordering      = ('-invoice_date', '-created_at')

    fieldsets = (
        ('Invoice', {
            'fields': ('gym', 'invoice_number', 'financial_year', 'invoice_date',
                       'invoice_type', 'status', 'cancellation_reason')
        }),
        ('Customer (snapshot)', {
            'fields': ('customer_name', 'customer_phone', 'customer_address',
                       'customer_gstin', 'customer_state', 'customer_state_code',
                       'place_of_supply_state', 'place_of_supply_code')
        }),
        ('Tax Totals', {
            'fields': ('taxable_value', 'cgst_amount', 'sgst_amount', 'igst_amount',
                       'round_off', 'grand_total')
        }),
        ('Links', {
            'fields': ('member', 'related_payment', 'related_order', 'pdf_url')
        }),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )

    def pdf_link(self, obj):
        if obj.pdf_url:
            return format_html('<a href="{}" target="_blank">📄 Download</a>', obj.pdf_url)
        return '—'
    pdf_link.short_description = 'PDF'


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display  = ('member_name', 'member_unique_id', 'gym', 'paid_amount',
                     'payment_method', 'payment_date', 'plan_name')
    list_filter   = ('gym', 'payment_method', 'payment_date')
    search_fields = ('member_name', 'member_phone', 'member_unique_id')
    ordering      = ('-payment_date', '-created_at')
    readonly_fields = ('created_at',)


@admin.register(InvoiceCounter)
class InvoiceCounterAdmin(admin.ModelAdmin):
    list_display = ('gym', 'financial_year', 'last_number')
    ordering     = ('gym', 'financial_year')


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'description', 'taxable_value', 'gst_rate')
    search_fields = ('invoice__invoice_number', 'description')

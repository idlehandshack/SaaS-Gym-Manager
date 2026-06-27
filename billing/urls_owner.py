# billing/urls_owner.py
from django.urls import path
from billing import views_owner

app_name = 'owner'

urlpatterns = [
    path('dashboard/',                  views_owner.owner_dashboard,                name='dashboard'),

    # Monthly reports — list from R2
    path('reports/',                    views_owner.owner_monthly_report_list,       name='report_list'),
    path('reports/generate/',           views_owner.owner_monthly_report_generate,   name='report_generate'),

    # Invoices
    path('invoices/',                   views_owner.owner_invoice_list,              name='invoice_list'),
    path('invoices/<int:pk>/pdf/',      views_owner.owner_invoice_pdf,               name='invoice_pdf'),
]
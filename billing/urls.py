from django.urls import path, include
from billing import views

app_name = 'billing'

urlpatterns = [
    # Existing routes
    path('invoice/<int:pk>/pdf/',       views.invoice_pdf_view,            name='invoice_pdf'),
    path('invoice/<int:pk>/pdf/regen/', views.invoice_pdf_regenerate_view,  name='invoice_pdf_regen'),
    path('gstr1/',                      views.gstr1_export_view,            name='gstr1_export'),
    path('payment/create/',             views.create_payment_view,          name='create_payment'),
    path('invoice/<int:invoice_pk>/refund/', views.issue_refund_view, name='issue_refund'),
    path('owner/send-invoice/', views.send_invoice_page, name='send_invoice_page'),
    path('owner/send-invoice/api/',views.send_invoice_api, name='send_invoice_api'),
    path('i/<str:token>/', views.public_invoice_view, name='public_invoice_view'),
    path('i/<str:token>/download/',views.public_invoice_download, name='public_invoice_download'),
    # Owner-only — nested under /billing/owner/
    path('owner/', include('billing.urls_owner')),
    
]
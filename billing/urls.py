from django.urls import path, include
from billing import views

app_name = 'billing'

urlpatterns = [
    # Existing routes
    path('invoice/<int:pk>/pdf/',       views.invoice_pdf_view,            name='invoice_pdf'),
    path('invoice/<int:pk>/pdf/regen/', views.invoice_pdf_regenerate_view,  name='invoice_pdf_regen'),
    path('gstr1/',                      views.gstr1_export_view,            name='gstr1_export'),
    path('payment/create/',             views.create_payment_view,          name='create_payment'),

    # Owner-only — nested under /billing/owner/
    path('owner/', include('billing.urls_owner')),
    
]
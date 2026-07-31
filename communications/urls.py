"""
communications/urls.py

Not wired into the project's root urls.py here — add a line like:

    path('communications/', include('communications.urls')),

to your project's urlconf (wherever announcements' own urls.py is
included) to activate these. Left out of this pass since editing the
project's root urlconf is outside the communications app itself.
"""

from django.urls import path

from . import api, views

urlpatterns = [
    # ── Super Admin screens ─────────────────────────────────────────────
    path('dashboard/', views.communication_dashboard, name='communication_dashboard'),
    path('', views.communication_list, name='communication_list'),
    path('create/', views.communication_create, name='communication_create'),
    path('<int:pk>/edit/', views.communication_edit, name='communication_edit'),
    path('<int:pk>/delete/', views.communication_delete, name='communication_delete'),
    path('<int:pk>/publish/', views.communication_publish_now, name='communication_publish_now'),
    path('<int:pk>/cancel/', views.communication_cancel, name='communication_cancel'),
    path('bulk-action/', views.communication_bulk_action, name='communication_bulk_action'),
    path('analytics/', views.communication_analytics, name='communication_analytics'),

    # ── Sponsors & Campaigns (Super Admin) ──────────────────────────────
    path('sponsors/', views.sponsor_list, name='sponsor_list'),
    path('sponsors/create/', views.sponsor_create, name='sponsor_create'),
    path('sponsors/<int:pk>/edit/', views.sponsor_edit, name='sponsor_edit'),
    path('sponsors/<int:pk>/delete/', views.sponsor_delete, name='sponsor_delete'),

    path('campaigns/', views.campaign_list, name='campaign_list'),
    path('campaigns/create/', views.campaign_create, name='campaign_create'),
    path('campaigns/<int:pk>/edit/', views.campaign_edit, name='campaign_edit'),
    path('campaigns/<int:pk>/delete/', views.campaign_delete, name='campaign_delete'),

    # ── Delivery Logs (Super Admin, read-only) ──────────────────────────
    path('delivery-logs/', views.delivery_log_list, name='communication_delivery_logs'),
    path('delivery-logs/export/', views.delivery_log_export, name='communication_delivery_logs_export'),
    
    # ── JSON API — Super Admin ───────────────────────────────────────────
    path('api/list/', api.api_list, name='communication_api_list'),
    path('api/<int:pk>/publish/', api.api_publish, name='communication_api_publish'),
    path('api/summary/', api.api_dashboard_summary, name='communication_api_summary'),

    # ── JSON API — recipient-facing (web widgets + Android app) ────────
    path('api/track/', api.api_track_event, name='communication_api_track'),
    path('api/center/', api.api_communication_center_list, name='communication_api_center_list'),
    path('api/home/', api.api_home, name='communication_api_home'),
    path('api/unread-count/', api.api_recipient_unread_count, name='communication_api_unread_count'),
]
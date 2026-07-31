from django.urls import path

from . import api, views

urlpatterns = [
    # ── Owner (Communication > Announcements) ───────────────────────────
    path('owner/announcements/', views.announcement_list, name='announcement_list'),
    path('owner/announcements/create/', views.announcement_create, name='announcement_create'),
    path('owner/announcements/<int:pk>/edit/', views.announcement_edit, name='announcement_edit'),
    path('owner/announcements/<int:pk>/delete/', views.announcement_delete, name='announcement_delete'),
    path('owner/announcements/<int:pk>/toggle-active/', views.announcement_toggle_active, name='announcement_toggle_active'),
    path('owner/announcements/<int:pk>/send-push/', views.announcement_send_push_now, name='announcement_send_push_now'),
    path('owner/announcements/archive/', views.announcement_archive, name='announcement_archive'),
    path('owner/announcements/analytics/', views.announcement_analytics, name='announcement_analytics'),

    # ── JSON APIs ─────────────────────────────────────────────────────────
    path('api/announcements/', api.api_list, name='api_announcements_list'),
    path('api/announcements/home/', api.api_home, name='api_announcements_home'),
    path('api/announcements/read/', api.api_mark_read, name='api_announcements_read'),
    path('api/announcements/dismiss/', api.api_mark_dismissed, name='api_announcements_dismiss'),
    path('api/announcements/unread-count/', api.api_unread_count, name='api_announcements_unread_count'),
]

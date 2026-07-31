# member_messages/urls.py
#
# Include this in your project's root urls.py, e.g.:
#     path('', include('member_messages.urls')),
#
from django.urls import path
from . import views

urlpatterns = [
    # ── Owner / Receptionist ────────────────────────────────────────────
    path('member-messages/', views.member_message_list, name='member_message_list'),
    path('member-messages/history/<int:member_id>/', views.member_message_history, name='member_message_history'),
    path('member-messages/send/', views.member_message_send, name='member_message_send'),
    path('member-messages/<int:message_id>/delete/', views.member_message_delete, name='member_message_delete'),

    # ── Member ───────────────────────────────────────────────────────────
    path('api/member-messages/', views.api_member_messages, name='api_member_messages'),
    path('api/member-messages/home/', views.api_member_messages_home, name='api_member_messages_home'),
    path('api/member-messages/read/', views.api_member_messages_read, name='api_member_messages_read'),
    path('api/member-messages/unread-count/', views.api_member_messages_unread_count, name='api_member_messages_unread_count'),
]

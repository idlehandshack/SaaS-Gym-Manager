from django.urls import path

from . import views

urlpatterns = [
    path('', views.notification_center, name='notification_center'),
    path('unread-count/', views.notification_center_unread_count, name='notification_center_unread_count'),
    path('<str:key>/read/', views.notification_center_mark_read, name='notification_center_mark_read'),
]
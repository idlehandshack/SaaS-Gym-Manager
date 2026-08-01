from django.contrib import admin
from django.urls import path ,include
from django.contrib.auth import views as auth_views
from django.conf import settings

urlpatterns = [
    path('admin/', admin.site.urls),
    path('',include("AuthFit.urls")),
    path('',include("Shop.urls")),
    path('push/', include('notifications.urls')),
    path('accounts/password_reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('accounts/password_reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('accounts/reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('', include('demoRequest.urls')),
    path('', include('Gym.urls')),
    path('', include('announcements.urls')),
    path('', include('member_messages.urls')),
    path('expenses/', include(('expenses.urls', 'expenses'), namespace='expenses')),
    path("communications/",include("communications.urls")),
    path('notifications/', include('notification_center.urls')),
    path("reviews/", include("reviews.urls")),
]

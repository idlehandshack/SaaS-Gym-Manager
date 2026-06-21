from django.urls import path, include
from AuthFit import views
from AuthFit.geo_views import geo_mark_attendance, serve_sw ,attendance_status
from . import device_views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', views.homePage, name='home'),
    path('accounts/password_reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('accounts/password_reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('accounts/reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('signup/', views.signupPage, name='signPage'),
    path('login/', views.loginPage, name='login'),
    path('workout/', views.workout, name='workout'),
    path('profile/', views.Profile, name='Profile'),
    path('logout/', views.handlelogout, name='logout'),
    path('contact/', views.contact, name='contact'),
    path('enrollment/', views.enrollment, name='enrollment'),
    path('attendence/', views.attendance_page, name='Attendence'),
    path('profile/upload-pic/', views.upload_profile_pic, name='upload_profile_pic'),
    path('renew-membership/', views.renew_membership, name='renew_membership'),
    path('freeze-membership/', views.freeze_membership,       name='freeze_membership'),
    path('freeze-membership/apply/',views.freeze_membership_apply, name='freeze_membership_apply'),
    path('membership-plans/', views.membership_plans, name='membership_plans'),
    path('contact-inquiries/', views.contact_inquiries, name='contact_inquiries'),

    # ── Existing APIs ──────────────────────────────────────────
    path('api/mark-attendance/', views.mark_attendance_api),
    path('api/get-users/', views.get_users),
    path('api/upload-face-image/', views.upload_face_image),
    path('api/stats/', views.stats_api, name='stats_api'),
    path('api/save-embeddings-batch/', views.save_embeddings_batch, name='save-embeddings-batch'),
    path('admin-tools/today-attendance/', views.today_attendance, name='today_attendance'),
    path('download/', views.download_app, name='download_app'),

    # ── NEW: Background geo auto-mark ─────────────────────────
    path('api/geo-mark-attendance/', geo_mark_attendance, name='geo_mark_attendance'),
    path('api/attendance-status/', attendance_status),
    path('sw.js', serve_sw, name='sw'),

    # ── Admin tools ────────────────────────────────────────────
    path('admin-tools/transferred-members/', views.transferred_members, name='transferred_members'),
    path('admin-tools/transferred-members/<int:transfer_id>/mark-inactive/', views.transfer_mark_inactive, name='transfer_mark_inactive'),
    path('admin-tools/transferred-members/<int:transfer_id>/delete/', views.transfer_delete_enrollment, name='transfer_delete_enrollment'),
    path('admin-tools/whatsapp/', views.whatsapp_pending_users, name='whatsapp_pending'),
    path('admin-tools/payments/', views.payment_management, name='payment_management'),
    path('admin-tools/update-payment/', views.update_payment, name='update_payment'),
    path('user-devices/register/',   device_views.register_user_device,   name='register_user_device'),
    path('user-devices/unregister/', device_views.unregister_user_device, name='unregister_user_device'),
    path('internal/run-expiry-check/', views.run_expiry_check, name='run_expiry_check'),
    path('ad/attendance/', views.attendance_analytics, name='attendance_analytics'),
    path('ad/revenue/', views.revenue_view, name='revenue'),
]
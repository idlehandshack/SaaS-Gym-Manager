from django.urls import path, reverse, include
from AuthFit import views
from AuthFit.geo_views import geo_mark_attendance, serve_sw, attendance_status
from . import device_views
from django.contrib.auth import views as auth_views
from Gym.views import (
    saas_dashboard, upi_payment_settings, all_gyms_view, add_gst_profile,
    add_staff_profile, gym_detail_json, add_gym_page, search_owner_by_phone,
    subscriptions_page, add_subscription_plan, edit_subscription_plan,
    delete_subscription_plan, change_gym_plan, platform_insights_page,
    api_kpi_summary, api_engagement_analytics,api_low_performing_gyms, api_member_distribution,
    api_member_growth, api_payment_analytics, api_platform_activity,
    api_platform_growth, api_renewal_churn, api_revenue_analytics,
    api_subscription_analytics, api_system_health, api_top_performing_gyms,
    api_dashboard, api_notifications,
)

from django.contrib.sitemaps.views import sitemap
from django.contrib.sitemaps import Sitemap


class StaticViewSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return ['home', 'signup', 'login', 'contact', 'enrollment', 'Attendence', 'download_app', 'profile', 'workout']

    def location(self, item):
        return reverse(item)


handler403 = 'AuthFit.views.custom_403_view'

urlpatterns = [
    path('', views.homePage, name='home'),
    path('accounts/password_reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('accounts/password_reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('accounts/reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('signup/', views.signupPage, name='signup'),
    path('login/', views.loginPage, name='login'),
    path('workout/', views.workout, name='workout'),
    path('profile/', views.Profile, name='profile'),
    path('logout/', views.handlelogout, name='logout'),
    path('contact/', views.contact, name='contact'),
    path('enrollment/', views.enrollment, name='enrollment'),
    path('attendence/', views.attendance_page, name='Attendence'),
    path('profile/upload-pic/', views.upload_profile_pic, name='upload_profile_pic'),
    path('renew-membership/', views.renew_membership, name='renew_membership'),
    path('freeze-membership/', views.freeze_membership, name='freeze_membership'),
    path('freeze-membership/apply/', views.freeze_membership_apply, name='freeze_membership_apply'),
    path('membership-plans/', views.membership_plans, name='membership_plans'),
    path('trainers/', views.trainers, name='trainers'),
    path('contact-inquiries/', views.contact_inquiries, name='contact_inquiries'),
    path('superadmin/dashboard/', saas_dashboard, name='saas_dashboard'),
    path('whychoseus/', views.feature_comp, name='whychoseus'),
    path('billing/', include('billing.urls')),
    path('', include('billing.urls_owner')),
    path('refundpolicy/', views.Refundpolicy, name='refundpolicy'),
    path('termandcondition/', views.termcondition, name='termandcondition'),
    path('privacypolicy/', views.privacypolicy, name='privacypolicy'),
    path('favicon.ico', views.gym_favicon, name='gym_favicon'),
    path('download/', views.download_app, name='download_app'),
    path('help/desktop/', views.guide, name='help'),
    path('quick-enrollment/', views.quick_enrollment, name='quick_enrollment'),
    path('complete-profile/', views.complete_profile, name='complete_profile'),
    path('settings/upi-payment/', upi_payment_settings, name='upi_payment_settings'),
    path('superadmin/all-gyms/', all_gyms_view, name='all_gyms'),
    path('gyms/add/', add_gym_page, name='add_gym_page'),
    path('gyms/search-owner/', search_owner_by_phone, name='search_owner_by_phone'),
    path('gyms/<uuid:gym_id>/detail/', gym_detail_json, name='gym_detail_json'),
    path('gyms/<uuid:gym_id>/add-staff/', add_staff_profile, name='add_staff_profile'),
    path('gyms/<uuid:gym_id>/gst-profile/', add_gst_profile, name='add_gst_profile'),
    path('subscriptions/', subscriptions_page, name='subscriptions_page'),
    path('subscriptions/plans/add/', add_subscription_plan, name='add_subscription_plan'),
    path('subscriptions/plans/<int:plan_id>/edit/', edit_subscription_plan, name='edit_subscription_plan'),
    path('subscriptions/plans/<int:plan_id>/delete/', delete_subscription_plan, name='delete_subscription_plan'),
    path('gyms/<uuid:gym_id>/change-plan/', change_gym_plan, name='change_gym_plan'),
    path('superadmin/platform-insights/', platform_insights_page, name='platform_insights'),

    # ── Platform Insights: aggregated (current) ─────────────────
    path('api/platform-insights/dashboard/', api_dashboard, name='api_pi_dashboard'),
    path('api/platform-insights/system-health/', api_system_health, name='api_pi_health'),
    path('api/platform-insights/notifications/', api_notifications, name='api_pi_notifications'),
    
    # ── Existing APIs ──────────────────────────────────────────
    path('api/mark-attendance/', views.mark_attendance_api),
    path('api/get-users/', views.get_users),
    path('api/upload-face-image/', views.upload_face_image),
    path('api/stats/', views.stats_api, name='stats_api'),
    path('api/save-embeddings-batch/', views.save_embeddings_batch, name='save-embeddings-batch'),
    path('admin-tools/today-attendance/', views.today_attendance, name='today_attendance'),
    path('api/embedding-version/', views.get_embedding_version, name='embedding-version'),
    path('api/gyms/login/', views.gym_login_api, name='gym_login_api'),
    path('aiattendance/', views.aiattendance, name='aiattendance'),
    path('owner/member/<int:enrollment_id>/change-plan/', views.change_membership_plan_view, name='change_membership_plan'),

    # ── Background geo auto-mark ─────────────────────────
    path('api/geo-mark-attendance/', geo_mark_attendance, name='geo_mark_attendance'),
    path('api/attendance-status/', attendance_status),
    path('sw.js', serve_sw, name='sw'),
    path('manifest.json', views.manifest, name='manifest'),

    # ── Admin tools ────────────────────────────────────────────
    path('admin-tools/transferred-members/', views.transferred_members, name='transferred_members'),
    path('admin-tools/transferred-members/<int:transfer_id>/mark-inactive/', views.transfer_mark_inactive, name='transfer_mark_inactive'),
    path('admin-tools/transferred-members/<int:transfer_id>/delete/', views.transfer_delete_enrollment, name='transfer_delete_enrollment'),
    path('admin-tools/whatsapp/', views.whatsapp_pending_users, name='whatsapp_pending'),
    path('admin-tools/payments/', views.payment_management, name='payment_management'),
    path('admin-tools/update-payment/', views.update_payment, name='update_payment'),
    path('user-devices/register/', device_views.register_user_device, name='register_user_device'),
    path('user-devices/unregister/', device_views.unregister_user_device, name='unregister_user_device'),
    path('internal/run-expiry-check/', views.run_expiry_check, name='run_expiry_check'),
    path('ad/attendance/', views.attendance_analytics, name='attendance_analytics'),
    path('ad/revenue/', views.revenue_view, name='revenue'),
    path('robots.txt', views.robots_txt, name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': {'static': StaticViewSitemap}}, name='django.contrib.sitemaps.views.sitemap'),
]
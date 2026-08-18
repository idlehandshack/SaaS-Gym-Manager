from django.urls import path, reverse, include
from django.contrib.auth import views as auth_views
from django.contrib.sitemaps.views import sitemap
from django.contrib.sitemaps import Sitemap

from AuthFit import views
from AuthFit import views_register_scan
from AuthFit.geo_views import serve_sw, attendance_status
from AuthFit.views_expiry_reminder import (
    SendExpiryReminderView,
    SendExpiryReminderPageView,
)
from AuthFit.decorators import active_member_required
from . import device_views
from AuthFit.qr_entry import qr_attendance_entry, qr_attendance_resume
from Gym.views import (
    saas_dashboard, upi_payment_settings, all_gyms_view, add_gst_profile,
    add_staff_profile, gym_detail_json, add_gym_page, search_owner_by_phone,
    subscriptions_page, add_subscription_plan, edit_subscription_plan,
    delete_subscription_plan, change_gym_plan, platform_insights_page,
    api_system_health, api_dashboard, api_notifications, record_platform_payment,
    enable_subscription_payment, disable_subscription_payment,
    confirm_subscription_payment, gym_payment_page, api_public_live_stats,
    plans_page, orphan_users_page, orphan_user_delete, orphan_user_bulk_delete,
    gym_qr_settings, gym_qr_regenerate, gym_qr_download, send_renewal_reminder,
    gst_profile_edit,data_deletion,renew_subscription ,toggle_gym_status,gym_quick_edit
)
from Gym.views_members import (
    member_list, member_detail, staff_mark_attendance, check_member_notifications,
)
from Gym.dashboard_views import dashboard_home, ai_credit_analysis ,tutorial_page

from AuthFit.services.userexcelsheet import export_enrollments_excel

class StaticViewSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return ['home', 'signup', 'login', 'contact', 'enrollment', 'Attendence', 'download_app', 'profile', 'workout']

    def location(self, item):
        return reverse(item)


handler403 = 'AuthFit.views.custom_403_view'


# ══════════════════════════════════════════════════════════════
# Core / Public Pages
# ══════════════════════════════════════════════════════════════
core_patterns = [
    path('', views.homePage, name='home'),
    path('contact/', views.contact, name='contact'),
    path('enrollment/', views.enrollment, name='enrollment'),
    path('trainers/', views.trainers, name='trainers'),
    path('whychoseus/', views.feature_comp, name='whychoseus'),
    path('membership-plans/', views.membership_plans, name='membership_plans'),
    path('refundpolicy/', views.Refundpolicy, name='refundpolicy'),
    path('termandcondition/', views.termcondition, name='termandcondition'),
    path('privacypolicy/', views.privacypolicy, name='privacypolicy'),
    path('favicon.ico', views.gym_favicon, name='gym_favicon'),
    path('download/', views.download_app, name='download_app'),
    path('help/desktop/', views.guide, name='help'),
    path('robots.txt', views.robots_txt, name='robots_txt'),
    path('manifest.json', views.manifest, name='manifest'),
    path('sw.js', serve_sw, name='sw'),
    path('sitemap.xml', sitemap, {'sitemaps': {'static': StaticViewSitemap}}, name='django.contrib.sitemaps.views.sitemap'),
    path('data-deletion/',data_deletion , name = "data-deletion"),
    path("superadmin/gym/<uuid:gym_id>/toggle-status/", toggle_gym_status, name="toggle_gym_status"),
    path("superadmin/gym/<uuid:gym_id>/quick-edit/", gym_quick_edit, name="gym_quick_edit"),
]


# ══════════════════════════════════════════════════════════════
# Authentication / Account
# ══════════════════════════════════════════════════════════════
auth_patterns = [
    path('signup/', views.signupPage, name='signup'),
    path('login/', views.loginPage, name='login'),
    path('logout/', views.handlelogout, name='logout'),
    path('accounts/password_reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('accounts/password_reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('accounts/reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('profile/update-email/', views.update_email, name='update_email'),
]


# ══════════════════════════════════════════════════════════════
# Member Profile & Membership
# ══════════════════════════════════════════════════════════════
profile_patterns = [
    path('profile/', views.Profile, name='profile'),
    path('profile/upload-pic/', views.upload_profile_pic, name='upload_profile_pic'),
    path('complete-profile/', views.complete_profile, name='complete_profile'),
    path('quick-enrollment/', views.quick_enrollment, name='quick_enrollment'),
    path('renew-membership/', views.renew_membership, name='renew_membership'),
    path('freeze-membership/', views.freeze_membership, name='freeze_membership'),
    path('freeze-membership/apply/', views.freeze_membership_apply, name='freeze_membership_apply'),
    path('inactivemember/', active_member_required, name='inactive_member'),
    path('gstproile/', gst_profile_edit, name='gst_profile_edit'),
    path('workout/', views.workout, name='workout'),
    path('needs-attention/', views.needs_attention, name='needs_attention'),
    path('tutorials/', tutorial_page, name='tutorial_page'),
    path('gym/<uuid:gym_id>/enrollments/export/',export_enrollments_excel, name='export_enrollments'),
]


# ══════════════════════════════════════════════════════════════
# Attendance (member-facing + AI)
# ══════════════════════════════════════════════════════════════
attendance_patterns = [
    path('attendence/', views.attendance_page, name='Attendence'),
    path('aiattendance/', views.aiattendance, name='aiattendance'),
    path('api/mark-attendance/', views.mark_attendance_api),
    path('api/attendance-status/', attendance_status),
    path('api/upload-face-image/', views.upload_face_image),
    path('api/save-embeddings-batch/', views.save_embeddings_batch, name='save-embeddings-batch'),
    path('api/embedding-version/', views.get_embedding_version, name='embedding-version'),
    path('api/get-users/', views.get_users),
    path('api/stats/', views.stats_api, name='stats_api'),
    path('attendance/qr/resume/', qr_attendance_resume, name='qr_attendance_resume'),
    path('attendance/qr/<str:qr_token>/', qr_attendance_entry, name='qr_attendance_entry'),
]


# ══════════════════════════════════════════════════════════════
# Billing (external apps)
# ══════════════════════════════════════════════════════════════
billing_patterns = [
    path('billing/', include('billing.urls')),
    path('', include(('billing.urls_owner', 'owner'), namespace='owner')),
]


# ══════════════════════════════════════════════════════════════
# Super Admin: SaaS / Platform
# ══════════════════════════════════════════════════════════════
superadmin_patterns = [
    path('superadmin/dashboard/', saas_dashboard, name='saas_dashboard'),
    path('superadmin/all-gyms/', all_gyms_view, name='all_gyms'),
    path('superadmin/platform-insights/', platform_insights_page, name='platform_insights'),
    path('superadmin/user-cleanup/', orphan_users_page, name='orphan_users_page'),
    path('superadmin/user-cleanup/<int:user_id>/delete/', orphan_user_delete, name='orphan_user_delete'),
    path('superadmin/user-cleanup/bulk-delete/', orphan_user_bulk_delete, name='orphan_user_bulk_delete'),
    path('superadmin/gym/<uuid:gym_id>/record-payment/', record_platform_payment, name='record_platform_payment'),
    path('super-admin/support-tickets/', views.login_support_tickets, name='login_support_tickets'),
    path('super-admin/support-tickets/<int:ticket_id>/resolve/', views.login_support_ticket_resolve, name='login_support_ticket_resolve'),
    path("gyms/<uuid:gym_id>/renew/",renew_subscription, name="renew_subscription"),
    # ── Platform Insights APIs ──
    path('api/platform-insights/dashboard/', api_dashboard, name='api_pi_dashboard'),
    path('api/platform-insights/system-health/', api_system_health, name='api_pi_health'),
    path('api/platform-insights/notifications/', api_notifications, name='api_pi_notifications'),
    path('api/public/live-stats/', api_public_live_stats, name='public_live_stats'),
]


# ══════════════════════════════════════════════════════════════
# Gyms (management, staff, plans)
# ══════════════════════════════════════════════════════════════
gym_patterns = [
    path('gyms/add/', add_gym_page, name='add_gym_page'),
    path('gyms/search-owner/', search_owner_by_phone, name='search_owner_by_phone'),
    path('gyms/<uuid:gym_id>/detail/', gym_detail_json, name='gym_detail_json'),
    path('gyms/<uuid:gym_id>/add-staff/', add_staff_profile, name='add_staff_profile'),
    path('gyms/<uuid:gym_id>/gst-profile/', add_gst_profile, name='add_gst_profile'),
    path('gyms/<uuid:gym_id>/change-plan/', change_gym_plan, name='change_gym_plan'),
    path('gyms/<uuid:gym_id>/payment/enable/', enable_subscription_payment, name='enable_subscription_payment'),
    path('gyms/<uuid:gym_id>/payment/disable/', disable_subscription_payment, name='disable_subscription_payment'),
    path('gyms/<uuid:gym_id>/payment/confirm/', confirm_subscription_payment, name='confirm_subscription_payment'),
    path('api/gyms/login/', views.gym_login_api, name='gym_login_api'),
    path('owner/gym-extras/', views.gym_extras, name='gym_extras'),
]


# ══════════════════════════════════════════════════════════════
# Subscriptions & Payments
# ══════════════════════════════════════════════════════════════
subscription_patterns = [
    path('subscriptions/', subscriptions_page, name='subscriptions_page'),
    path('subscriptions/plans/add/', add_subscription_plan, name='add_subscription_plan'),
    path('subscriptions/plans/<int:plan_id>/edit/', edit_subscription_plan, name='edit_subscription_plan'),
    path('subscriptions/plans/<int:plan_id>/delete/', delete_subscription_plan, name='delete_subscription_plan'),
    path('settings/upi-payment/', upi_payment_settings, name='upi_payment_settings'),
    path('pay-subscription/', gym_payment_page, name='gym_payment_page'),
    path('plans/', plans_page, name='plans_page'),
]


# ══════════════════════════════════════════════════════════════
# Owner: Members & Enrollment
# ══════════════════════════════════════════════════════════════
owner_member_patterns = [
    path('owner/member/<int:enrollment_id>/delete/', views.delete_enrollment_view, name='delete_enrollment'),
    path('owner/member/<int:enrollment_id>/change-plan/', views.change_membership_plan_view, name='change_membership_plan'),
]


# ══════════════════════════════════════════════════════════════
# Owner: Attendance QR
# ══════════════════════════════════════════════════════════════
owner_qr_patterns = [
    path('owner/attendance/qr/', gym_qr_settings, name='gym_qr_settings'),
    path('owner/attendance/qr/regenerate/', gym_qr_regenerate, name='gym_qr_regenerate'),
    path('owner/attendance/qr/download/', gym_qr_download, name='gym_qr_download'),
    path('owner/attendance/attempts/<int:attempt_id>/remind/', send_renewal_reminder, name='send_renewal_reminder'),
]


# ══════════════════════════════════════════════════════════════
# Owner: Register Scan (Attendance Import)
# ══════════════════════════════════════════════════════════════
owner_register_scan_patterns = [
    path('owner/attendance/register-scan/upload/', views_register_scan.register_scan_upload, name='register_scan_upload'),
    path('owner/attendance/register-scan/search-members/', views_register_scan.register_scan_member_search, name='register_scan_member_search'),
    path('owner/attendance/register-scan/validate/', views_register_scan.register_scan_validate, name='register_scan_validate'),
    path('owner/attendance/register-scan/save/', views_register_scan.register_scan_save, name='register_scan_save'),
    path('owner/attendance/register-scan/history/', views_register_scan.register_scan_history_page, name='register_scan_history_page'),
    path('owner/attendance/register-scan/history/list/', views_register_scan.register_scan_history_list, name='register_scan_history_list'),
    path('owner/attendance/register-scan/history/<int:import_id>/', views_register_scan.register_scan_history_detail, name='register_scan_history_detail'),
]


# ══════════════════════════════════════════════════════════════
# Members (Staff-facing management)
# ══════════════════════════════════════════════════════════════
members_patterns = [
    path('members/', member_list, name='member_list'),
    path('members/<int:member_id>/', member_detail, name='member_detail'),
    path('members/<int:member_id>/renew/', views.staff_renew_membership, name='staff_renew_membership'),
    path('members/<int:member_id>/edit/', views.staff_edit_member, name='staff_edit_member'),
    path('members/<int:member_id>/mark-attendance/', staff_mark_attendance, name='staff_mark_attendance'),
    path('members/<int:member_id>/check-notifications/', check_member_notifications, name='check_member_notifications'),
]


# ══════════════════════════════════════════════════════════════
# Admin Tools
# ══════════════════════════════════════════════════════════════
admin_tools_patterns = [
    path('admin-tools/today-attendance/', views.today_attendance, name='today_attendance'),
    path('admin-tools/transferred-members/', views.transferred_members, name='transferred_members'),
    path('admin-tools/transferred-members/<int:transfer_id>/mark-inactive/', views.transfer_mark_inactive, name='transfer_mark_inactive'),
    path('admin-tools/transferred-members/<int:transfer_id>/delete/', views.transfer_delete_enrollment, name='transfer_delete_enrollment'),
    path('admin-tools/whatsapp/', views.whatsapp_pending_users, name='whatsapp_pending'),
    path('admin-tools/payments/', views.payment_management, name='payment_management'),
    path('admin-tools/update-payment/', views.update_payment, name='update_payment'),
    path('admin-tools/expiry-reminders/', SendExpiryReminderPageView.as_view(), name='expiry-reminders-page'),
    path('api/send-expiry-reminders/', SendExpiryReminderView.as_view(), name='send-expiry-reminders'),
    path('internal/run-expiry-check/', views.run_expiry_check, name='run_expiry_check'),
]


# ══════════════════════════════════════════════════════════════
# Devices
# ══════════════════════════════════════════════════════════════
device_patterns = [
    path('user-devices/register/', device_views.register_user_device, name='register_user_device'),
    path('user-devices/unregister/', device_views.unregister_user_device, name='unregister_user_device'),
]


# ══════════════════════════════════════════════════════════════
# Dashboards & Analytics
# ══════════════════════════════════════════════════════════════
dashboard_patterns = [
    path('dashboards/', dashboard_home, name='dashboard_home'),
    path('dashboard/ai-credits/analysis/', ai_credit_analysis, name='ai_credit_analysis'),
    path('ad/attendance/', views.attendance_analytics, name='attendance_analytics'),
    path('ad/revenue/', views.revenue_view, name='revenue'),
]


# ══════════════════════════════════════════════════════════════
# Invoices & Support
# ══════════════════════════════════════════════════════════════
misc_patterns = [
    path('invoice/<int:pk>/pdf/', views.invoice_pdf_view, name='invoice_pdf_view'),
    path('contact-inquiries/', views.contact_inquiries, name='contact_inquiries'),
    path('support/submit/', views.login_support_submit, name='login_support_submit'),
]


urlpatterns = (
    core_patterns
    + auth_patterns
    + profile_patterns
    + attendance_patterns
    + billing_patterns
    + superadmin_patterns
    + gym_patterns
    + subscription_patterns
    + owner_member_patterns
    + owner_qr_patterns
    + owner_register_scan_patterns
    + members_patterns
    + admin_tools_patterns
    + device_patterns
    + dashboard_patterns
    + misc_patterns
)
from django.urls import path

from Gym.staff_permission_views import (
    staff_permissions_list,
    staff_permissions_edit,
    staff_permissions_reset_defaults,
)
from Gym.whatsapp_views import (
        whatsapp_settings, whatsapp_verify, whatsapp_disconnect,
        whatsapp_send_test, whatsapp_status,
    )
urlpatterns = [
    path(
        "owner/staff-permissions/",
        staff_permissions_list,
        name="staff_permissions_list",
    ),
    path(
        "owner/staff-permissions/<int:staff_id>/edit/",
        staff_permissions_edit,
        name="staff_permissions_edit",
    ),
    path(
        "owner/staff-permissions/<int:staff_id>/reset/",
        staff_permissions_reset_defaults,
        name="staff_permissions_reset_defaults",
    ),
    path('owner/whatsapp/settings/', whatsapp_settings, name='whatsapp_settings'),
    path('owner/whatsapp/verify/', whatsapp_verify, name='whatsapp_verify'),
    path('owner/whatsapp/disconnect/', whatsapp_disconnect, name='whatsapp_disconnect'),
    path('owner/whatsapp/send-test/', whatsapp_send_test, name='whatsapp_send_test'),
    path('owner/whatsapp/status/', whatsapp_status, name='whatsapp_status'),
]
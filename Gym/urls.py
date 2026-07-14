from django.urls import path

from Gym.staff_permission_views import (
    staff_permissions_list,
    staff_permissions_edit,
    staff_permissions_reset_defaults,
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
]
# AuthFit/permissions.py
#
# Add this permission class alongside your existing DRF permissions.
# It does not replace IsAuthenticated — chain both on the view, e.g.
# permission_classes = [IsAuthenticated, IsGymOwnerOrReceptionist]

from rest_framework.permissions import BasePermission
import functools
import logging

from django.core.exceptions import PermissionDenied

from Gym.models import StaffPermission, PERMISSION_DEFINITIONS

logger = logging.getLogger(__name__)

ALLOWED_EXPIRY_REMINDER_ROLES = {'gym_owner', 'receptionist'}

PERMISSION_GROUPS = {}
for _field_name, _label, _group in PERMISSION_DEFINITIONS:
    PERMISSION_GROUPS.setdefault(_group, []).append((_field_name, _label))
del _field_name, _label, _group

ALL_PERMISSION_FIELDS = [f for f, _, _ in PERMISSION_DEFINITIONS]


def has_permission(request, permission_name: str) -> bool:
    """
    Central permission check.

    - Superuser / super_admin  -> always True
    - gym_owner                -> always True
    - everyone else            -> looked up from StaffPermission, False if missing
    """
    if getattr(request.user, "is_superuser", False):
        return True

    if getattr(request, "is_super_admin", False):
        return True

    if getattr(request, "staff_role", None) == "gym_owner":
        return True

    staff_profile = getattr(request.user, "staff_profile", None)
    if staff_profile is None:
        return False

    try:
        perms = staff_profile.permissions
    except StaffPermission.DoesNotExist:
        logger.warning(
            "StaffPermission missing for staff_profile_id=%s — defaulting to no access.",
            staff_profile.pk,
        )
        return False

    if permission_name not in ALL_PERMISSION_FIELDS:
        # Fail closed on typos instead of silently granting access.
        logger.error("Unknown permission_name requested: %s", permission_name)
        return False

    return bool(getattr(perms, permission_name, False))

class IsGymOwnerOrReceptionist(BasePermission):
    """
    Allows access only to authenticated staff whose StaffProfile.role is
    'gym_owner' or 'receptionist'. Trainers, members, and anonymous
    users are rejected.

    Assumes request.user.staff_profile exists for staff accounts (same
    relation already used elsewhere in this codebase, e.g.
    user__staff_profile__role in Shop/notifications.py). If the user has
    no staff_profile at all (e.g. a plain member account), access is
    denied rather than raising.
    """

    message = "Only gym owners and receptionists can send expiry reminders."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False

        staff_profile = getattr(user, 'staff_profile', None)
        if staff_profile is None:
            return False

        return staff_profile.role in ALLOWED_EXPIRY_REMINDER_ROLES
    

def permission_required(permission_name: str):
    """
    Usage:
        @permission_required("can_delete_enrollment")
        def delete_member(request, ...): ...

    Requires login + gym-staff status first (reuses your existing
    _gym_staff_required so tenant isolation / staff gating stays identical),
    then checks the specific permission. Raises PermissionDenied on failure.
    """
    def decorator(view_fn):
        # Local import avoids a circular import (AuthFit.views also imports
        # from this module in the refactor below).
        from AuthFit.views import _gym_staff_required

        @_gym_staff_required
        @functools.wraps(view_fn)
        def wrapped(request, *args, **kwargs):
            if not has_permission(request, permission_name):
                raise PermissionDenied(
                    f"You don't have the '{permission_name}' permission."
                )
            return view_fn(request, *args, **kwargs)
        return wrapped
    return decorator
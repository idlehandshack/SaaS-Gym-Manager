# AuthFit/permissions.py
#
# Add this permission class alongside your existing DRF permissions.
# It does not replace IsAuthenticated — chain both on the view, e.g.
# permission_classes = [IsAuthenticated, IsGymOwnerOrReceptionist]

from rest_framework.permissions import BasePermission

ALLOWED_EXPIRY_REMINDER_ROLES = {'gym_owner', 'receptionist'}


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
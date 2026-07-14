# Gym/staff_permission_views.py (new file)
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from AuthFit.views import _gym_role_required
from AuthFit.permissions import PERMISSION_GROUPS, ALL_PERMISSION_FIELDS
from .models import StaffProfile, StaffPermission


@_gym_role_required("gym_owner")
def staff_permissions_list(request):
    """List all receptionists/trainers for the owner's gym."""
    gym = getattr(request, "gym", None)
    if gym is None:
        raise PermissionDenied("No gym context available.")

    staff = (
        StaffProfile.objects
        .filter(gym=gym, role__in=("receptionist", "trainer"))
        .select_related("user", "permissions")
        .order_by("role", "user__username")
    )
    return render(request, "staff_permissions_list.html", {
        "gym": gym,
        "staff": staff,
    })


@_gym_role_required("gym_owner")
def staff_permissions_edit(request, staff_id):
    """Grouped-checkbox editor for one receptionist/trainer."""
    gym = getattr(request, "gym", None)
    if gym is None:
        raise PermissionDenied("No gym context available.")

    # Tenant isolation: only staff belonging to THIS gym, never another gym's.
    staff_profile = get_object_or_404(
        StaffProfile, pk=staff_id, gym=gym, role__in=("receptionist", "trainer")
    )
    perms, _ = StaffPermission.objects.get_or_create(staff_profile=staff_profile)

    if request.method == "POST":
        for field_name in ALL_PERMISSION_FIELDS:
            setattr(perms, field_name, request.POST.get(field_name) == "on")
        perms.updated_by = request.user
        perms.save()
        messages.success(
            request, f"Permissions updated for {staff_profile.user.username}."
        )
        return redirect("staff_permissions_list")

    return render(request, "staff_permissions_edit.html", {
        "gym": gym,
        "staff_profile": staff_profile,
        "perms": perms,
        "groups": PERMISSION_GROUPS,
    })


@_gym_role_required("gym_owner")
@require_POST
def staff_permissions_reset_defaults(request, staff_id):
    """Owner convenience action: reset a staff member back to role defaults."""
    gym = getattr(request, "gym", None)
    staff_profile = get_object_or_404(
        StaffProfile, pk=staff_id, gym=gym, role__in=("receptionist", "trainer")
    )
    perms, _ = StaffPermission.objects.get_or_create(staff_profile=staff_profile)
    perms.apply_role_defaults(staff_profile.role)
    perms.updated_by = request.user
    perms.save()
    messages.success(request, f"Reset {staff_profile.user.username} to role defaults.")
    return redirect("staff_permissions_list")
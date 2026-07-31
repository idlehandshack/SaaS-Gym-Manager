"""
announcements/permissions.py

Small, dependency-free permission helpers. Deliberately NOT class-based /
DRF-based, to match the plain-function decorator style already used across
the project (see AuthFit views' `@_gym_role_required`, Gym.views'
`@superuser_required`).

Rules from the spec:
    SuperAdmin    -> every gym, every announcement
    GymOwner      -> own gym only
    Trainer       -> no access at all
    Receptionist  -> read-only, and only if StaffPermission grants it
    Member        -> read + mark-read/dismiss only, never create/edit
"""

import functools

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from Gym.models import StaffProfile


def get_staff_profile(user):
    return getattr(user, 'staff_profile', None)


def can_manage_announcements(user) -> bool:
    """True for super admins and gym owners. Trainers -> False. Receptionists -> False (manage, not view)."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = get_staff_profile(user)
    return bool(profile and profile.active and profile.role == 'gym_owner')


def can_view_announcements_admin(user) -> bool:
    """
    True for anyone allowed into the Communication > Announcements screens,
    including read-only receptionists (gated by StaffPermission).
    """
    if can_manage_announcements(user):
        return True
    profile = get_staff_profile(user)
    if not profile or not profile.active or profile.role != 'receptionist':
        return False
    perms = getattr(profile, 'permissions', None)
    return bool(perms and getattr(perms, 'can_manage_notifications', False))


def resolve_gym_for_staff(user):
    """SuperAdmin passing ?gym=<id> is handled at the view layer; this
    resolves the *default* gym for a scoped staff member."""
    if user.is_superuser:
        return None
    profile = get_staff_profile(user)
    return profile.gym if profile else None


def announcement_admin_required(view_func):
    """Gate for the owner CRUD/analytics pages. Ensures request.gym is set
    correctly for GymOwner/Receptionist (SuperAdmin may switch gyms)."""
    @login_required
    @functools.wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not can_view_announcements_admin(request.user):
            raise PermissionDenied("You do not have access to Announcements.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def announcement_write_required(view_func):
    """Gate for create/edit/delete/publish actions — stricter than view access."""
    @login_required
    @functools.wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not can_manage_announcements(request.user):
            raise PermissionDenied("You do not have permission to modify announcements.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def get_announcement_or_404(request, pk):
    """Fetch an Announcement, enforcing tenant isolation for non-superadmins."""
    from .models import Announcement
    qs = Announcement.objects.select_related('gym')
    if not request.user.is_superuser:
        gym = resolve_gym_for_staff(request.user)
        qs = qs.filter(gym=gym)
    return get_object_or_404(qs, pk=pk)

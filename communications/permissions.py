"""
communications/permissions.py

Per the spec's `permissions` block, this whole module is Super Admin only —
gym_owner / trainer / receptionist / member all have access=False. That's a
much simpler gate than announcements/permissions.py needs (which juggles
per-gym staff roles); we just need "is this a Django superuser".

Reuses the project's existing `superuser_required` decorator if it's
importable from its real location — adjust SUPERUSER_REQUIRED_IMPORT_PATH
below to match. Falls back to an equivalent local definition so this file
still works standalone if the import path is wrong.
"""

import logging

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse

logger = logging.getLogger(__name__)

try:
    # NOTE: adjust this import to wherever `superuser_required` actually
    # lives in the project (it was supplied without a module path). Reusing
    # it here rather than redefining it, per the "reuse existing utility
    # methods" rule.
    from Gym.decorators import superuser_required  # noqa: F401
except ImportError:
    from django.contrib.auth.decorators import login_required

    def superuser_required(view_func):
        """Only Django superusers can pass. Everyone else gets 403."""
        @login_required
        def wrapper(request, *args, **kwargs):
            if not request.user.is_superuser:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return wrapper


def superuser_required_json(view_func):
    """
    JSON-API variant of superuser_required — returns a 403 JsonResponse
    instead of raising PermissionDenied with an HTML error page, matching
    how announcements/api.py's endpoints are consumed by JS/mobile clients.
    """
    from django.contrib.auth.decorators import login_required

    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            return JsonResponse({"ok": False, "error": "Super Admin access required."}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


def is_super_admin(user) -> bool:
    return bool(user and user.is_authenticated and user.is_superuser)

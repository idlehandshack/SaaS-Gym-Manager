"""
Gym/decorators.py
-----------------
Reusable feature-flag decorators for per-gym module toggles.
"""

import functools
from django.http import HttpResponseForbidden
from django.contrib import messages
from django.shortcuts import redirect


def _feature_required(flag_name, redirect_url='/', error_msg=None):
    """
    Generic factory. Returns a decorator that checks gym.<flag_name>.
    If the flag is False, returns 403 for AJAX or redirects with a message.
    """
    def decorator(view_fn):
        @functools.wraps(view_fn)
        def wrapper(request, *args, **kwargs):
            gym = getattr(request, 'gym', None)

            # Superuser always passes — they manage all gyms
            if request.user.is_superuser:
                return view_fn(request, *args, **kwargs)

            # No gym context at all → let the view handle it normally
            if gym is None:
                return view_fn(request, *args, **kwargs)

            if not getattr(gym, flag_name, True):
                msg = error_msg or f"This feature is disabled for {gym.gym_name}."

                is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                if is_ajax:
                    from django.http import JsonResponse
                    return JsonResponse({'error': msg}, status=403)

                messages.error(request, msg)
                return redirect(redirect_url)

            return view_fn(request, *args, **kwargs)
        return wrapper
    return decorator


# ── Ready-made decorators for each module ─────────────────────────────────────

def store_enabled_required(view_fn):
    return _feature_required(
        'enable_store',
        redirect_url='/',
        error_msg="Supplement store is not available for this gym.",
    )(view_fn)


def attendance_enabled_required(view_fn):
    return _feature_required(
        'enable_attendance',
        redirect_url='/',
        error_msg="Attendance module is not available for this gym.",
    )(view_fn)


def trainers_enabled_required(view_fn):
    return _feature_required(
        'enable_trainers',
        redirect_url='/',
        error_msg="Trainer management is not available for this gym.",
    )(view_fn)

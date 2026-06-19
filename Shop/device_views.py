# shop/device_views.py
"""
Endpoints called by the React Native WebView to register/unregister FCM tokens.

Registration is made from inside the owner's WebView (not a direct RN fetch),
so the request carries:
  - A valid Django session  → request.user = authenticated staff owner
  - The gym subdomain       → GymMiddleware sets request.gym correctly
  - A CSRF token            → read from cookie by the injected JS

Add to shop/urls.py:
    path('devices/register/',   views_device.register_device,   name='register_device'),
    path('devices/unregister/', views_device.unregister_device, name='unregister_device'),
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import StaffDevice

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# register_device
# ---------------------------------------------------------------------------

@login_required
@require_POST
def register_device(request):
    """
    Register (or reactivate) a staff FCM token.

    Called from inside the owner's WebView via injected JavaScript, so:
      - request.user is the authenticated staff owner (@login_required ensures this)
      - request.gym  is set by GymMiddleware from the subdomain
      - CSRF is validated automatically by Django (no @csrf_exempt needed)

    This guarantees StaffDevice is saved with the correct gym + user, so
    notifications.py's filter(gym=gym, active=True) matches only this
    tenant's devices — no cross-gym notification leakage.
    """
    # ── gym context ─────────────────────────────────────────────────────────
    gym = getattr(request, 'gym', None)
    if not gym:
        logger.error(
            "register_device: request.gym is None for user_id=%s. "
            "Check GymMiddleware is installed and the correct subdomain is used.",
            request.user.id,
        )
        return JsonResponse(
            {'ok': False, 'error': 'No gym context. Use your gym subdomain.'},
            status=400,
        )

    # ── parse body ──────────────────────────────────────────────────────────
    try:
        body        = json.loads(request.body)
        fcm_token   = body.get('token', '').strip()
        device_name = body.get('device_name', '').strip()[:120]
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON.'}, status=400)

    if not fcm_token:
        return JsonResponse({'ok': False, 'error': 'token is required.'}, status=400)

    # ── persist ─────────────────────────────────────────────────────────────
    # @login_required guarantees request.user is authenticated.
    # gym is validated above. Both are always saved — no conditional guards needed.
    obj, created = StaffDevice.objects.update_or_create(
        fcm_token=fcm_token,
        defaults={
            'gym':         gym,
            'user':        request.user,
            'device_name': device_name,
            'active':      True,
        },
    )

    logger.info(
        "register_device: user_id=%s gym=%s fcm_token=%.20s… created=%s",
        request.user.id,
        getattr(gym, 'gym_code', gym),
        fcm_token,
        created,
    )
    return JsonResponse({'ok': True, 'created': created})


# ---------------------------------------------------------------------------
# unregister_device
# ---------------------------------------------------------------------------

@login_required
@require_POST
def unregister_device(request):
    """
    Deactivate a staff FCM token.

    Scoped by fcm_token + user + gym — a request from goldengym cannot
    deactivate a token belonging to ironhouse or a different user.
    """
    # ── parse body ──────────────────────────────────────────────────────────
    try:
        body      = json.loads(request.body)
        fcm_token = body.get('token', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON.'}, status=400)

    if not fcm_token:
        return JsonResponse({'ok': False, 'error': 'token is required.'}, status=400)

    # ── scoped deactivation ─────────────────────────────────────────────────
    gym = getattr(request, 'gym', None)

    updated = StaffDevice.objects.filter(
        fcm_token=fcm_token,
        user=request.user,
        gym=gym,
    ).update(active=False)

    logger.info(
        "unregister_device: user_id=%s gym=%s fcm_token=%.20s… deactivated=%s",
        request.user.id,
        getattr(gym, 'gym_code', gym),
        fcm_token,
        updated > 0,
    )
    return JsonResponse({'ok': True, 'deactivated': updated > 0})
# AuthFit/device_views.py
"""
Endpoints called by the React Native app to register/unregister a member's
FCM token for plan-expiry push notifications.

All device records are gym-scoped — a token registered at ironhouse
is only used for ironhouse notifications, never fitzone's.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import UserDevice

logger = logging.getLogger(__name__)


@login_required
@require_POST
def register_user_device(request):
    """
    Register (or reactivate) an FCM token for the current user at their gym.

    The gym comes from request.gym (set by GymMiddleware from the subdomain).
    A token registered at ironhouse.entergym.in is stamped with ironhouse's
    gym FK — it will never receive fitzone's notifications.
    """
    gym = getattr(request, 'gym', None)
    if not gym:
        return JsonResponse(
            {'ok': False, 'error': 'No gym context. Use your gym subdomain.'},
            status=400,
        )

    try:
        body        = json.loads(request.body)
        fcm_token   = body.get('token', '').strip()
        device_name = body.get('device_name', '').strip()[:120]
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON.'}, status=400)

    if not fcm_token:
        return JsonResponse({'ok': False, 'error': 'token is required.'}, status=400)

    # update_or_create on fcm_token — tokens are globally unique (per FCM spec)
    # We also set gym so the token is scoped to this tenant
    obj, created = UserDevice.objects.update_or_create(
        fcm_token=fcm_token,
        defaults={
            'user':        request.user,
            'gym':         gym,            # ← gym-scoped
            'device_name': device_name,
            'active':      True,
        },
    )

    logger.info(
        "register_user_device: user_id=%s gym=%s created=%s",
        request.user.id, gym.gym_code, created,
    )
    return JsonResponse({'ok': True, 'created': created})


@login_required
@require_POST
def unregister_user_device(request):
    """
    Deactivate an FCM token for the current user.

    Filters by both fcm_token AND user AND gym — prevents a user from
    deactivating a token that belongs to another user or another gym.
    """
    gym = getattr(request, 'gym', None)

    try:
        body      = json.loads(request.body)
        fcm_token = body.get('token', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON.'}, status=400)

    if not fcm_token:
        return JsonResponse({'ok': False, 'error': 'token is required.'}, status=400)

    # Scope filter: user + gym + token
    # Without gym scoping, a user could deactivate a token at another gym
    qs = UserDevice.objects.filter(fcm_token=fcm_token, user=request.user)
    if gym:
        qs = qs.filter(gym=gym)

    updated = qs.update(active=False)

    logger.info(
        "unregister_user_device: user_id=%s gym=%s deactivated=%s",
        request.user.id, getattr(gym, 'gym_code', 'none'), updated > 0,
    )
    return JsonResponse({'ok': True, 'deactivated': updated > 0})
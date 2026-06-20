# notification/views.py

import json
import logging

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import WebPushSubscription

logger = logging.getLogger(__name__)


# ── Decorator order note ──────────────────────────────────────────────────────
# @csrf_exempt must be the OUTERMOST decorator (applied last = listed first).
# If @login_required wraps first, the CSRF check runs before exempt can skip it,
# causing CSRF failures on the push subscription endpoint from the browser SW.
# Correct order: csrf_exempt → login_required → require_POST
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@login_required
@require_POST
def save_subscription(request):
    """
    Browser calls this after the user allows notifications.
    Creates or reactivates a WebPushSubscription for the current user.
    """
    try:
        data = json.loads(request.body)

        endpoint = data.get("endpoint", "").strip()
        p256dh   = data.get("keys", {}).get("p256dh", "").strip()
        auth     = data.get("keys", {}).get("auth", "").strip()
        if not all([endpoint, p256dh, auth]):
            return JsonResponse({"error": "Missing fields: endpoint, p256dh, auth required."}, status=400)

        # FIX: include active=True in defaults so a previously deactivated
        # subscription (marked inactive after a 410) gets reactivated when
        # the user re-subscribes in the same browser.
        obj, created = WebPushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                "user":   request.user,
                "p256dh": p256dh,
                "auth":   auth,
                "active": True,   # reactivate if it was marked inactive
            },
        )

        logger.info(
            "Web push subscription %s for user_id=%s",
            "created" if created else "updated",
            request.user.id,
        )
        return JsonResponse({"status": "ok", "created": created})

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    except Exception:
        logger.exception("Error saving web push subscription for user_id=%s", request.user.id)
        return JsonResponse({"error": "Internal error."}, status=500)


@csrf_exempt
@login_required
@require_POST
def delete_subscription(request):
    """
    Browser calls this when the user blocks notifications.

    FIX: original had no user filter — anyone could delete any subscription
    by guessing an endpoint URL. Now scoped to request.user so a user can
    only deactivate their own subscriptions.

    Marks active=False instead of deleting — keeps audit trail consistent
    with how utils.py handles 410 responses.
    """
    try:
        data     = json.loads(request.body)
        endpoint = data.get("endpoint", "").strip()

        if not endpoint:
            return JsonResponse({"error": "endpoint is required."}, status=400)

        # FIX: filter by user — prevents unauthorized deletion of other users' subs
        updated = WebPushSubscription.objects.filter(
            endpoint=endpoint,
            user=request.user,      # scope to current user only
        ).update(active=False)      # mark inactive, don't delete

        logger.info(
            "Web push subscription deactivated for user_id=%s found=%s",
            request.user.id, updated > 0,
        )
        return JsonResponse({"status": "deleted", "found": updated > 0})

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    except Exception:
        logger.exception("Error deleting web push subscription for user_id=%s", request.user.id)
        return JsonResponse({"error": "Internal error."}, status=500)


@staff_member_required
def test_push(request):
    """
    Dev/debug endpoint — sends a test web push to the requesting staff user.
    Verifies the full VAPID + pywebpush stack is wired correctly.

    Only accessible to is_staff users. Remove or gate behind DEBUG in production.
    """
    from notifications.utils import send_web_push
    send_web_push(
        user=request.user,
        title="Test Notification",
        body="Web push is working on EnterGYM!",
        url="/",
    )
    return HttpResponse("Push sent! Check your browser.")
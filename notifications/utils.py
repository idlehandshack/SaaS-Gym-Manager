# notifications/utils.py
"""
Web-push helpers (browser / PWA subscriptions via pywebpush).

Public API:
    send_web_push(user, title, body, url) -> int
        Push to one user's active browser subscriptions.
        Returns number of successful deliveries (0 if none).

    send_web_push_to_gym_staff(gym, title, body, url) -> int
        Push to all staff subscribed at a specific gym.
        Returns total number of successful deliveries across all staff.
"""

import json
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from pywebpush import webpush, WebPushException

from .models import WebPushSubscription

logger = logging.getLogger(__name__)


def send_web_push(user, title: str, body: str, url: str = "/") -> int:
    """
    Send a web push notification to all ACTIVE browser/PWA subscriptions
    of one user.

    Returns:
        int — number of subscriptions that accepted the push successfully.
              0 if the user has no active subscriptions, or all fail.

    On HTTP 410 Gone (subscription permanently expired):
        -> marks subscription active=False instead of deleting
        -> keeps audit trail, avoids re-pushing to dead endpoints

    On other errors:
        -> logs warning, continues to next subscription, does not count
           the failed delivery toward the return value
    """
    subscriptions = WebPushSubscription.objects.filter(
        user=user,
        active=True,
    )
    logger.warning(
    "WEB PUSH DEBUG user=%s subs=%s",
    user.username,
    subscriptions.count(),
)
    success_count = 0

    for sub in subscriptions:
        logger.warning(
    "Sending to endpoint %.80s",
    sub.endpoint
)
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {
                        "p256dh": sub.p256dh,
                        "auth":   sub.auth,
                    },
                },
                data=json.dumps({
                    "title": title,
                    "body":  body,
                    "url":   url,
                }),
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims=settings.VAPID_CLAIMS,
            )

            success_count += 1

            sub.last_used = timezone.now()
            sub.save(update_fields=['last_used'])

            logger.info(
                "Web push sent to user_id=%s endpoint=%.50s",
                user.id, sub.endpoint,
            )
            logger.warning(
    "SUCCESS endpoint %.80s",
    sub.endpoint
)

        except WebPushException as e:
            response_status = getattr(e.response, "status_code", None)
            logger.warning(
                "Web push failed for user_id=%s status=%s error=%s body=%s",
                user.id, response_status, e,
                getattr(e.response, "text", None),
            )

            if response_status == 410:
                # 410 Gone = browser permanently unsubscribed.
                # Mark inactive rather than delete — keeps audit trail.
                sub.active = False
                sub.save(update_fields=['active'])
            # Other errors (5xx, network) — leave subscription active,
            # will retry on next push. Do NOT increment success_count.

        except Exception:
            logger.exception(
                "Unexpected web push error for user_id=%s", user.id
            )
            # Do NOT increment success_count.

    if success_count == 0 and subscriptions.exists():
        logger.warning(
            "send_web_push: user_id=%s had active subscriptions "
            "but all deliveries failed",
            user.id,
        )

    return success_count


def send_web_push_to_gym_staff(
    gym, title: str, body: str, url: str = "/"
) -> int:
    """
    Send a web push to every staff member who has an active browser
    subscription AND belongs to the given gym.

    Returns:
        int — total number of successful browser push deliveries
              across all staff users at this gym.
              0 if no staff have active subscriptions, or all fail.

    Tenant isolation: scoped to StaffProfile → gym.
    fitzone staff never receive ironhouse alerts.
    """
    if gym is None:
        # Platform-level alert — only for superuser/SaaS owner alerts,
        # NOT for gym-specific events like expiry or new enrollments.
        staff_users = User.objects.filter(is_staff=True, is_active=True)
        logger.info(
            "send_web_push_to_gym_staff: gym=None, "
            "targeting all platform staff (%d users)",
            staff_users.count(),
        )
    else:
        from Gym.models import StaffProfile
        staff_user_ids = (
            StaffProfile.objects
            .filter(gym=gym, active=True, role='receptionist')
            .values_list('user_id', flat=True)
        )
        staff_users = User.objects.filter(
            id__in=staff_user_ids,
            is_active=True,
        )
        logger.info(
            "send_web_push_to_gym_staff: gym=%s targeting %d staff users",
            getattr(gym, 'gym_code', gym), staff_users.count(),
        )

    total_successes = 0

    for user in staff_users:
        # send_web_push returns 0 on failure — safe to accumulate directly.
        total_successes += send_web_push(user, title, body, url)

    logger.info(
        "send_web_push_to_gym_staff: gym=%s total_successes=%d",
        getattr(gym, 'gym_code', gym), total_successes,
    )

    return total_successes
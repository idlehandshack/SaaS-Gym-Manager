# notification/utils.py
"""
Web-push helpers (browser / PWA subscriptions via pywebpush).

Public API:
    send_web_push(user, title, body, url)
        -> push to one user's active browser subscriptions

    send_web_push_to_gym_staff(gym, title, body, url)
        -> push to all staff subscribed at a specific gym
           replaces the old send_web_push_to_all_staff() which was
           a multi-tenancy violation (sent to ALL staff across ALL gyms)
"""

import json
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from pywebpush import webpush, WebPushException

from .models import WebPushSubscription

logger = logging.getLogger(__name__)


def send_web_push(user, title, body, url="/"):
    """
    Send a web push notification to all ACTIVE browser/PWA subscriptions
    of one user.

    On HTTP 410 Gone (subscription permanently expired):
        -> marks subscription active=False instead of deleting
        -> keeps audit trail, avoids re-pushing to dead endpoints

    On other errors:
        -> logs warning and continues to next subscription
    """
    subscriptions = WebPushSubscription.objects.filter(
        user=user,
        active=True,        # FIX: was missing -- was pushing to expired subs
    )

    for sub in subscriptions:
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

            # FIX: track last successful push for audit/debugging
            sub.last_used = timezone.now()
            sub.save(update_fields=['last_used'])

            logger.info(
                "Web push sent to user_id=%s endpoint=%.50s",
                user.id, sub.endpoint,
            )

        except WebPushException as e:
            response_status = getattr(e.response, "status_code", None)
            logger.warning(
                "Web push failed for user_id=%s status=%s error=%s body=%s",
                user.id, response_status, e,
                getattr(e.response, "text", None),
            )

            if response_status == 410:
                # 410 Gone = browser permanently unsubscribed
                # FIX: was sub.delete() -- mark inactive instead to keep audit trail
                sub.active = False
                sub.save(update_fields=['active'])
            # Other errors (5xx, network) -- leave subscription active,
            # will retry on next push

        except Exception:
            logger.exception(
                "Unexpected web push error for user_id=%s", user.id
            )


def send_web_push_to_gym_staff(gym, title, body, url="/"):
    """
    Send a web push to every staff member who has an active browser
    subscription AND belongs to the given gym.

    FIX: replaces send_web_push_to_all_staff() which was a multi-tenancy
    violation -- it sent to ALL is_staff=True users across ALL gyms.

    Args:
        gym:   Gym instance (the tenant). Pass None only for
               platform-level superuser alerts.
        title: Notification title string.
        body:  Notification body string.
        url:   URL to open when the notification is clicked.
    """
    if gym is None:
        # Platform-level alert -- only for superuser/SaaS owner alerts
        # NOT for gym-specific events like expiry or new enrollments
        staff_users = User.objects.filter(is_staff=True, is_active=True)
        logger.info(
            "send_web_push_to_gym_staff: gym=None, "
            "targeting all platform staff (%d users)",
            staff_users.count(),
        )
    else:
        # FIX: scope to staff belonging to this specific gym only
        from Gym.models import StaffProfile
        staff_user_ids = (
            StaffProfile.objects
            .filter(gym=gym, active=True)
            .values_list('user_id', flat=True)
        )
        staff_users = User.objects.filter(
            id__in=staff_user_ids,
            is_active=True,
        )
        logger.info(
            "send_web_push_to_gym_staff: gym=%s targeting %d staff users",
            gym.gym_code, staff_users.count(),
        )

    for user in staff_users:
        send_web_push(user, title, body, url)
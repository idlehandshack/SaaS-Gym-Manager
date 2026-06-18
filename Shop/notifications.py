# Shop/notifications.py
"""
Push-notification helpers for EnterGYM.

Usage (always call via transaction.on_commit):
    from Shop.notifications import notify_staff_new_order
    transaction.on_commit(lambda: notify_staff_new_order(order))

    from Shop.notifications import notify_staff_new_enrollment
    transaction.on_commit(lambda: notify_staff_new_enrollment(enrollment))
"""

import logging
import firebase_admin
from firebase_admin import credentials, messaging
from django.conf import settings

from .models import StaffDevice
from notifications.utils import send_web_push_to_gym_staff

logger = logging.getLogger(__name__)


# ── Init Firebase app once ────────────────────────────────────────────────────

def _get_firebase_app():
    """Return the default Firebase app, initialising it on first call."""
    if not firebase_admin._apps:
        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
        firebase_admin.initialize_app(cred)
    return firebase_admin.get_app()


# ── Core FCM send helper ──────────────────────────────────────────────────────

def send_push_to_tokens(
    tokens: list[str],
    title: str,
    body: str,
    data: dict = None,
    channel_id: str = 'entergym_orders',
) -> int:
    """
    Send a multicast FCM push to a list of tokens.
    Returns the number of successes.
    Silently deactivates tokens FCM reports as invalid/expired.
    """
    if not tokens:
        return 0

    _get_firebase_app()

    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=title, body=body),
        data={str(k): str(v) for k, v in (data or {}).items()},
        android=messaging.AndroidConfig(
            priority='high',
            notification=messaging.AndroidNotification(
                icon='ic_notification',
                color='#ff4d00',
                channel_id=channel_id,
            ),
        ),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound='default', badge=1)
            )
        ),
    )

    try:
        response = messaging.send_each_for_multicast(message)
        logger.info(
            "FCM multicast: %d success, %d failure (out of %d tokens)",
            response.success_count, response.failure_count, len(tokens),
        )
        _prune_bad_tokens(tokens, response)
        return response.success_count
    except Exception:
        logger.exception("FCM send failed for %d tokens", len(tokens))
        return 0


def _prune_bad_tokens(tokens: list[str], response) -> None:
    """
    Deactivate FCM tokens FCM reports as UNREGISTERED or INVALID_ARGUMENT.
    Acts on the specific bad tokens returned — no gym filter needed here
    since we're responding to FCM's explicit rejection, not querying broadly.
    """
    from AuthFit.models import UserDevice  # local import avoids circular import

    bad = []
    for idx, result in enumerate(response.responses):
        if not result.success and result.exception:
            code = getattr(result.exception, 'code', '')
            if code in ('UNREGISTERED', 'INVALID_ARGUMENT'):
                bad.append(tokens[idx])

    if bad:
        removed_staff = StaffDevice.objects.filter(fcm_token__in=bad).update(active=False)
        removed_user  = UserDevice.objects.filter(fcm_token__in=bad).update(active=False)
        logger.info(
            "Deactivated stale FCM tokens — staff:%d user:%d",
            removed_staff, removed_user,
        )


# ── Gym-scoped staff token helper ─────────────────────────────────────────────

def _get_staff_tokens(gym) -> list[str]:
    """
    Return active FCM tokens for staff belonging to the given gym only.

    Now works correctly because StaffDevice has both gym FK and user FK.
    Previously StaffDevice had no gym FK so this returned tokens from
    ALL gyms — every staff notification went to all gyms' staff.
    """
    if not gym:
        logger.warning("_get_staff_tokens called with gym=None — returning empty")
        return []

    return list(
        StaffDevice.objects
        .filter(gym=gym, active=True)       # gym-scoped
        .values_list('fcm_token', flat=True)
    )


# ── Domain-specific notifications ─────────────────────────────────────────────

def notify_staff_new_order(order) -> None:
    """
    Push a 'new order' alert to every active staff device at this order's gym.

    Call via transaction.on_commit() after order is saved:
        transaction.on_commit(lambda: notify_staff_new_order(order))

    order.gym is now available because Order model has gym FK.
    """
    gym         = order.gym
    flavor_part = f" ({order.flavor.name})" if order.flavor else ""
    customer    = order.user.get_full_name().strip() or order.user.username

    title = f"New Order #{order.id}"
    body  = (
        f"{customer} ordered {order.product.name}{flavor_part} "
        f"x {order.quantity} - Rs.{int(order.total_price)}"
    )

    # ── Web push → staff browser/PWA ─────────────────────────────────────
    try:
        send_web_push_to_gym_staff(
            gym=gym,
            title=title,
            body=body,
            url="/shop/orders/admin/",
        )
    except Exception:
        logger.exception(
            "notify_staff_new_order: web push failed for gym=%s",
            getattr(gym, 'gym_code', None),
        )

    # ── FCM push → staff mobile app ───────────────────────────────────────
    tokens = _get_staff_tokens(gym)
    if not tokens:
        logger.debug(
            "notify_staff_new_order: no active staff devices for gym=%s",
            getattr(gym, 'gym_code', None),
        )
        return

    send_push_to_tokens(
        tokens=tokens,
        title=title,
        body=body,
        data={
            "order_id": str(order.id),
            "gym_id":   str(gym.id),
            "screen":   "AdminOrders",
            "type":     "new_order",
        },
        channel_id='entergym_orders',
    )


def notify_staff_new_enrollment(enrollment) -> None:
    """
    Push a 'new member joined' alert to every active staff device at this gym.

    Call via transaction.on_commit() after enrollment is saved:
        transaction.on_commit(lambda: notify_staff_new_enrollment(enrollment))

    enrollment.gym is available because Enrollment model has gym FK.
    """
    gym       = enrollment.gym
    plan_name = enrollment.selectPlan.plan if enrollment.selectPlan else "a plan"
    gender    = "M" if enrollment.gender == "M" else "F"

    title = f"New Member Joined! ({gender})"
    body  = (
        f"{enrollment.fullname} enrolled at {gym.gym_name} "
        f"with the {plan_name} plan."
    )

    # ── Web push → staff browser/PWA ─────────────────────────────────────
    try:
        send_web_push_to_gym_staff(
            gym=gym,
            title=title,
            body=body,
            url="/admin-tools/payments/",
        )
    except Exception:
        logger.exception(
            "notify_staff_new_enrollment: web push failed for gym=%s",
            getattr(gym, 'gym_code', None),
        )

    # ── FCM push → staff mobile app ───────────────────────────────────────
    tokens = _get_staff_tokens(gym)
    if not tokens:
        logger.debug(
            "notify_staff_new_enrollment: no active staff devices for gym=%s",
            getattr(gym, 'gym_code', None),
        )
        return

    send_push_to_tokens(
        tokens=tokens,
        title=title,
        body=body,
        data={
            "enrollment_id": str(enrollment.id),
            "unique_id":     enrollment.unique_id,
            "gym_id":        str(gym.id),
            "screen":        "MemberDetail",
            "type":          "new_enrollment",
        },
        channel_id='entergym_orders',
    )
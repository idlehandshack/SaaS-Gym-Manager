# Shop/notifications_stock.py
"""
Low-stock alerting for GymProductFlavor. Hooks into StockMovementService
so every stock-changing operation automatically checks the threshold.

De-duplication: a notification is only sent once per "low stock episode" —
i.e. once stock crosses <= minimum_stock, we don't alert again until the
stock has been replenished back above minimum_stock and drops again.
This is tracked via GymProductFlavor.low_stock_notified (added below).
"""

import logging

logger = logging.getLogger(__name__)


def check_low_stock(flavor) -> None:
    """
    Called after every stock mutation. Sends a staff notification the
    moment stock crosses at-or-below minimum_stock, and resets the flag
    once stock recovers above minimum_stock so future dips alert again.
    """
    from .models import GymProductFlavor

    is_low = flavor.stock <= flavor.minimum_stock

    if is_low and not flavor.low_stock_notified:
        _send_low_stock_alert(flavor)
        GymProductFlavor.objects.filter(pk=flavor.pk).update(low_stock_notified=True)
    elif not is_low and flavor.low_stock_notified:
        # Stock replenished — rearm for the next time it dips
        GymProductFlavor.objects.filter(pk=flavor.pk).update(low_stock_notified=False)


def _send_low_stock_alert(flavor) -> None:
    from .notifications import send_push_to_tokens, _get_staff_tokens

    gym_product = flavor.gym_product
    gym = gym_product.gym
    product_name = gym_product.global_product.name  # CHANGED — no custom_name dependency
    flavor_name = flavor.global_flavor.flavor_name

    title = "Low Stock Alert"
    body = (
        f"{product_name} ({flavor_name}) is low — "
        f"{flavor.stock} left (minimum {flavor.minimum_stock})."
    )

    try:
        from notifications.utils import send_web_push_to_gym_staff
        send_web_push_to_gym_staff(gym=gym, title=title, body=body, url="/shop/my-store/")
    except Exception:
        logger.exception("Low-stock web push failed for gym=%s", getattr(gym, 'gym_code', None))

    tokens = _get_staff_tokens(gym)
    if not tokens:
        return

    send_push_to_tokens(
        tokens=tokens,
        title=title,
        body=body,
        data={
            "gym_product_flavor_id": str(flavor.pk),
            "gym_id": str(gym.id),
            "screen": "GymStoreManage",
            "type": "low_stock",
        },
        channel_id='entergym_orders',
    )
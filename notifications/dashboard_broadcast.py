# notifications/dashboard_broadcast.py
"""
Single entry point for broadcasting lightweight "something changed" events
to a gym's connected dashboard tabs. This is NOT attendance-specific — it's
the general-purpose sibling of attendance_broadcast.py, reusing the same
channel layer but its own group so the two systems can never cross-talk.

Usage from anywhere data changes (views, signals, services):
    from notifications.dashboard_broadcast import broadcast_dashboard_event
    broadcast_dashboard_event(gym, "member_created", member_id=enrollment.id)

Never raises — a broadcast failure must never break the write that already
committed successfully.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def broadcast_dashboard_event(gym, event_type: str, **extra) -> None:
    """
    gym:        Gym instance. No-op if None (platform-level events aren't
                in scope for this system).
    event_type: one of the keys in the frontend PAGE_REGISTRY
                (e.g. "member_created", "payment_added", "attendance_marked").
    **extra:    small, optional bits of context (e.g. member_id) — kept
                minimal on purpose. This is a "go re-fetch" signal, not a
                data payload; pages that refresh pull fresh data themselves.
    """
    try:
        if gym is None:
            return

        channel_layer = get_channel_layer()
        if channel_layer is None:
            logger.warning("broadcast_dashboard_event: no channel layer configured, skipping")
            return

        payload = {"event": event_type, **extra}

        async_to_sync(channel_layer.group_send)(
            f"dashboard_gym_{gym.id}",
            {"type": "dashboard.notification", "payload": payload},
        )
        logger.info("Dashboard event broadcast gym=%s event=%s", gym.id, event_type)

    except Exception:
        logger.exception(
            "broadcast_dashboard_event: failed gym=%s event=%s",
            getattr(gym, 'id', None), event_type,
        )
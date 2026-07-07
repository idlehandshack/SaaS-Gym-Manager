"""
Thin dispatcher over the existing Shop.notifications senders.

This does NOT replace or duplicate the push/web-push implementation —
it's a single, named entry point so callers (DemoRequest, and future
event sources) don't need to know which underlying sender to call.
Add new event constants here as new features arrive; the actual
send logic still lives in Shop/notifications.py.
"""
import logging

logger = logging.getLogger("notifications")


class NotificationEvent:
    """Event-type constants, reused as the 'type' field in push payloads."""
    DEMO_REQUEST = "demo_request"
    # Future events — add here, not in DemoRequest or any other feature app:
    # NEW_GYM_REGISTRATION = "new_gym_registration"
    # SUBSCRIPTION_PURCHASED = "subscription_purchased"
    # PAYMENT_RECEIVED = "payment_received"
    # SUPPORT_TICKET = "support_ticket"
    # SERVER_ALERT = "server_alert"
    # DAILY_SALES_SUMMARY = "daily_sales_summary"


class NotificationService:
    """
    Single entry point for feature apps to trigger admin-facing pushes.
    Currently backed by Shop.notifications.notify_superadmins; swapping
    or extending the backend later only requires changes here.
    """

    @staticmethod
    def send_superadmin_notification(
        event_type: str,
        title: str,
        body: str,
        data: dict = None,
        url: str = "/superadmin/dashboard/",
    ) -> bool:
        from Shop.notifications import notify_superadmins

        payload = {**(data or {}), "type": event_type}
        return notify_superadmins(title=title, body=body, data=payload, url=url)
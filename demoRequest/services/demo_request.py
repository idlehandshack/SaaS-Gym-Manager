import logging
from datetime import timedelta
from typing import Optional

from django.http import HttpRequest
from django.utils import timezone

from ..models import DemoRequest
from ..forms import DemoRequestForm

logger = logging.getLogger("demoRequest")

DUPLICATE_WINDOW_HOURS = 24


class DuplicateDemoRequestError(Exception):
    """Raised when a phone number already has a recent pending request."""


def _get_client_ip(request: HttpRequest) -> Optional[str]:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _has_recent_duplicate(phone_number: str) -> bool:
    cutoff = timezone.now() - timedelta(hours=DUPLICATE_WINDOW_HOURS)
    return DemoRequest.objects.filter(
        phone_number=phone_number,
        created_at__gte=cutoff,
    ).exists()


def _send_demo_request_notification(demo_request: DemoRequest) -> None:
    """
    Notify superadmins that a new demo request came in. Failures here
    must never roll back the DemoRequest save — this is fire-and-log,
    with push_sent recording the outcome for later inspection/retry.
    """
    from notifications.services import NotificationService, NotificationEvent

    logger.info("Demo request notification started: id=%s", demo_request.id)

    title = "🏋️ New Demo Request"
    body = (
        f"New demo request received.\n"
        f"Gym: {demo_request.gym_name}\n"
        f"Owner: {demo_request.owner_name}\n"
        f"Phone: {demo_request.phone_number}\n"
        f"Language: {demo_request.get_preferred_language_display()}\n"
        f"Tap to view details."
    )

    try:
        delivered = NotificationService.send_superadmin_notification(
            event_type=NotificationEvent.DEMO_REQUEST,
            title=title,
            body=body,
            data={
                "demo_request_id": str(demo_request.id),
                "screen": "SuperadminDemoRequests",
            },
            url="/superadmin/dashboard/",
        )
    except Exception:
        logger.exception(
            "Demo request notification: unexpected exception id=%s",
            demo_request.id,
        )
        delivered = False

    if delivered:
        logger.info("Notification delivered successfully: id=%s", demo_request.id)
    else:
        logger.warning("Notification failed: id=%s", demo_request.id)

    DemoRequest.objects.filter(pk=demo_request.pk).update(push_sent=delivered)
    demo_request.push_sent = delivered


def create_demo_request(form: DemoRequestForm, request: HttpRequest) -> DemoRequest:
    """
    Persist a validated DemoRequestForm as a DemoRequest record.

    Raises:
        DuplicateDemoRequestError: if the phone number already has a
            request within the last 24 hours.
    """
    phone_number = form.cleaned_data["phone_number"]

    if _has_recent_duplicate(phone_number):
        logger.info("Duplicate demo request blocked for phone=%s", phone_number)
        raise DuplicateDemoRequestError(
            "You've already requested a demo. Our team will contact you shortly."
        )

    demo_request: DemoRequest = form.save(commit=False)
    demo_request.ip_address = _get_client_ip(request)
    demo_request.user_agent = request.META.get("HTTP_USER_AGENT", "")[:500]
    demo_request.save()

    logger.info(
        "New demo request created: id=%s gym=%s phone=%s",
        demo_request.id, demo_request.gym_name, phone_number,
    )

    # Notification failures are logged and reflected in push_sent —
    # they never affect the already-committed DemoRequest row.
    _send_demo_request_notification(demo_request)

    # TODO: Queue email using Celery -> send_demo_request_confirmation_email.delay(demo_request.id)
    # TODO: WhatsApp API -> WhatsAppService.notify_new_lead(demo_request)

    return demo_request
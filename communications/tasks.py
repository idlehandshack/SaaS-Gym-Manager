"""
communications/tasks.py

Plain functions rather than @shared_task — this project's cron jobs
(AuthFit.notifications.send_expiry_reminders, announcements' scheduled
publish) are all invoked via management commands, not Celery. Kept as
plain functions here too so `management/commands/publish_scheduled_communications.py`
can call them directly, and so they can be wrapped in `@shared_task` with
one line if Celery is introduced later without moving the logic.
"""

import logging

from django.utils import timezone

from .models import Communication
from .services import dispatch_communication

logger = logging.getLogger(__name__)


def publish_due_communications() -> dict:
    """
    Dispatches every Communication whose scheduled publish_at has arrived
    and hasn't been dispatched yet. Mirrors the isolation philosophy used
    throughout AuthFit/notifications.py — one bad communication is logged
    and skipped, never aborts the run for the rest.
    """
    now = timezone.now()
    due = Communication.objects.filter(
        status__in=[Communication.Status.DRAFT, Communication.Status.SCHEDULED],
        is_active=True,
        publish_at__lte=now,
        dispatched_at__isnull=True,
    )

    dispatched, failed = 0, 0
    for communication in due:
        try:
            if not hasattr(communication, 'audience'):
                logger.warning(
                    "publish_due_communications: communication=%s has no audience configured — skipping",
                    communication.id,
                )
                continue
            result = dispatch_communication(communication)
            if result.get('skipped'):
                logger.warning("communication=%s skipped: %s", communication.id, result['skipped'])
                continue
            if result['success'] == 0 and result['failure'] == 0:
                logger.warning("communication=%s dispatched to zero recipients", communication.id)
            dispatched += 1
        except Exception:
            logger.exception(
                "publish_due_communications: failed to dispatch communication=%s — will retry next run",
                communication.id,
            )
            failed += 1
            continue

    logger.info("publish_due_communications: dispatched=%d failed=%d", dispatched, failed)
    return {'dispatched': dispatched, 'failed': failed}


def expire_stale_communications() -> int:
    """Flips is_active off for anything past its expires_at — pure
    housekeeping, doesn't touch dispatch counts or delivery logs."""
    now = timezone.now()
    updated = Communication.objects.filter(
        is_active=True, expires_at__isnull=False, expires_at__lt=now,
    ).exclude(status=Communication.Status.EXPIRED).update(
        status=Communication.Status.EXPIRED, is_active=False,
    )
    if updated:
        logger.info("expire_stale_communications: expired %d communication(s)", updated)
    return updated

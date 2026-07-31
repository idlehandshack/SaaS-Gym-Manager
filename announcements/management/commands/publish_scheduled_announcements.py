"""
Run every few minutes via cron/systemd-timer alongside the existing
expiry-reminder cron (see AuthFit/notifications.py send_expiry_reminders,
called from AuthFit's own scheduled task).

Responsibilities:
1. Any announcement that has just crossed its publish_at and has
   send_push=True but hasn't been pushed yet -> send push, once.
2. Nothing else — expiry is handled at *query time* (is_live / is_expired
   properties + the `is_active & publish_at & expires_at` filters used
   throughout views/api), so there is deliberately no "mark expired" write
   here. hide_after_expiry from the spec is satisfied by every visibility
   query already excluding expired rows; no batch job needs to touch them.
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from announcements.models import Announcement
from announcements.utils import send_announcement_push

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send push notifications for announcements that just became live."

    def handle(self, *args, **options):
        now = timezone.now()
        due = Announcement.objects.filter(
            is_active=True,
            send_push=True,
            publish_at__lte=now,
            push_sent_at__isnull=True,
        )
        # exclude already-expired announcements that were scheduled in the past
        due = due.exclude(expires_at__lt=now)

        sent_total = 0
        for announcement in due:
            try:
                sent = send_announcement_push(announcement)
                sent_total += sent
                logger.info(
                    "publish_scheduled_announcements: pushed announcement=%s gym=%s successes=%s",
                    announcement.id, announcement.gym_id, sent,
                )
            except Exception:
                logger.exception(
                    "publish_scheduled_announcements: failed announcement=%s", announcement.id,
                )
                continue

        self.stdout.write(self.style.SUCCESS(
            f"Processed {due.count()} newly-live announcement(s), {sent_total} push deliveries."
        ))

"""
Run on a schedule (e.g. every 5-15 minutes via cron/Render Cron Job),
same convention as AuthFit's expiry-reminder cron:

    python manage.py publish_scheduled_communications
"""

from django.core.management.base import BaseCommand

from communications.tasks import expire_stale_communications, publish_due_communications


class Command(BaseCommand):
    help = "Dispatch due (scheduled) communications and expire stale ones."

    def handle(self, *args, **options):
        result = publish_due_communications()
        expired = expire_stale_communications()
        self.stdout.write(self.style.SUCCESS(
            f"Dispatched {result['dispatched']} communication(s), "
            f"{result['failed']} failed, {expired} expired."
        ))

from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.utils import timezone


class Command(BaseCommand):
    help = "Send expiry reminder push notifications to members"

    def handle(self, *args, **kwargs):
        from AuthFit.notifications import send_expiry_reminders

        count = send_expiry_reminders()

        # Heartbeat for System Health dashboard
        cache.set("cron:last_run", timezone.now(), timeout=None)

        self.stdout.write(
            self.style.SUCCESS(
                f"Expiry reminders sent for {count} enrollments"
            )
        )
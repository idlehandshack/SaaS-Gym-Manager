from django.core.management.base import BaseCommand
from django.utils import timezone

from expenses import services


class Command(BaseCommand):
    help = "Backfill/generate this month's recurring expenses for all active ExpenseTemplates, across all gyms. Run daily (or at least on the 1st) via cron/systemd timer."

    def add_arguments(self, parser):
        parser.add_argument(
            '--gym-id', type=int, default=None,
            help="Limit generation to a single gym (for manual testing).",
        )

    def handle(self, *args, **options):
        gym = None
        if options['gym_id']:
            from Gym.models import Gym
            try:
                gym = Gym.objects.get(pk=options['gym_id'])
            except Gym.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Gym {options['gym_id']} not found."))
                return

        created = services.generate_recurring_expenses(gym=gym, today=timezone.localdate())

        if not created:
            self.stdout.write(self.style.SUCCESS("No new recurring expenses to generate. All templates up to date."))
            return

        self.stdout.write(self.style.SUCCESS(f"Generated {len(created)} recurring expense(s):"))
        for exp in created:
            self.stdout.write(f"  - {exp.gym} | {exp.title} | ₹{exp.amount} | {exp.expense_date}")
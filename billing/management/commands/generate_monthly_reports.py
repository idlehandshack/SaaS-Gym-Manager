"""
billing/management/commands/generate_monthly_reports.py
---------------------------------------------------------
Django management command that generates monthly revenue reports
for every active gym and uploads them to Cloudflare R2.

Designed to run on the 1st of each month via cron / Render cron job.

Usage:
    # Generate previous month's report for ALL gyms (normal cron use):
    python manage.py generate_monthly_reports

    # Generate a specific month for ALL gyms:
    python manage.py generate_monthly_reports --year 2026 --month 5

    # Generate for a single gym (useful for testing / manual re-runs):
    python manage.py generate_monthly_reports --gym-code fitzone

    # Force regenerate even if the file already exists in R2:
    python manage.py generate_monthly_reports --force
"""
from datetime import date

from django.core.management.base import BaseCommand

from Gym.models import Gym
from billing.services.monthly_report_store import (
    generate_and_store_monthly_report,
    get_report_url,
)


class Command(BaseCommand):
    help = 'Generate monthly revenue reports for all gyms and upload to Cloudflare R2'

    def add_arguments(self, parser):
        parser.add_argument(
            '--year', type=int, default=None,
            help='Year (default: previous month\'s year)',
        )
        parser.add_argument(
            '--month', type=int, default=None,
            help='Month 1-12 (default: previous month)',
        )
        parser.add_argument(
            '--gym-code', type=str, default=None,
            help='Only process this gym (by gym_code)',
        )
        parser.add_argument(
            '--force', action='store_true', default=False,
            help='Regenerate even if the report already exists in R2',
        )

    def handle(self, *args, **options):
        today = date.today()

        # Default: previous month
        if options['month'] is None or options['year'] is None:
            if today.month == 1:
                year, month = today.year - 1, 12
            else:
                year, month = today.year, today.month - 1
        else:
            year  = options['year']
            month = options['month']

        if not (1 <= month <= 12):
            self.stderr.write(self.style.ERROR(f"Invalid month: {month}"))
            return

        target_label = date(year, month, 1).strftime('%B %Y')
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Generating monthly reports for {target_label}..."
            )
        )

        # Resolve which gyms to process
        gym_qs = Gym.objects.filter(active=True)
        if options['gym_code']:
            gym_qs = gym_qs.filter(gym_code=options['gym_code'])
            if not gym_qs.exists():
                self.stderr.write(
                    self.style.ERROR(f"No active gym with code '{options['gym_code']}'")
                )
                return

        ok_count   = 0
        skip_count = 0
        fail_count = 0

        for gym in gym_qs:
            # Skip if already exists (unless --force)
            if not options['force']:
                existing_url = get_report_url(gym, year, month)
                if existing_url:
                    self.stdout.write(
                        f"  SKIP  {gym.gym_code:<20} already exists: {existing_url}"
                    )
                    skip_count += 1
                    continue

            try:
                url = generate_and_store_monthly_report(gym, year, month)
                self.stdout.write(
                    self.style.SUCCESS(f"  OK    {gym.gym_code:<20} {url}")
                )
                ok_count += 1
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"  FAIL  {gym.gym_code:<20} {exc}")
                )
                fail_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Generated: {ok_count}  Skipped: {skip_count}  Failed: {fail_count}"
            )
        )
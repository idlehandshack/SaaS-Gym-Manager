"""
Backfills User.email for accounts where it's blank, using the best
available source: Gym.contact_email for owners, Enrollment.email for
members. Never overwrites an existing non-blank email. Skips values that
don't look like a valid email (e.g. garbage data, address fragments).
Safe to re-run — already-filled accounts are skipped automatically.

Usage:
    python manage.py backfill_user_emails            # dry run, shows what would change
    python manage.py backfill_user_emails --apply     # actually writes the changes
"""
import re
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from AuthFit.models import Enrollment

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class Command(BaseCommand):
    help = "Backfill blank User.email from Gym.contact_email (owners) or Enrollment.email (members)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually save changes. Without this flag, only prints what would change.',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        blank_users = User.objects.filter(email='')

        updated_owners = []
        updated_members = []
        still_blank = []
        skipped_invalid = []

        for user in blank_users:
            owned_gym = getattr(user, 'owned_gym', None)

            if owned_gym and owned_gym.contact_email:
                candidate = owned_gym.contact_email.strip()
                if EMAIL_RE.match(candidate):
                    updated_owners.append((user, candidate))
                    continue
                else:
                    skipped_invalid.append((user, candidate))
                    continue

            enrollment = (
                Enrollment.objects
                .filter(user=user)
                .exclude(email='')
                .exclude(email__isnull=True)
                .first()
            )
            if enrollment and enrollment.email:
                candidate = enrollment.email.strip()
                if EMAIL_RE.match(candidate):
                    updated_members.append((user, candidate))
                    continue
                else:
                    skipped_invalid.append((user, candidate))
                    continue

            still_blank.append(user)

        self.stdout.write(self.style.WARNING(
            f"\n{'APPLYING' if apply_changes else 'DRY RUN — no changes will be saved'}\n"
        ))

        self.stdout.write(f"Owners to update ({len(updated_owners)}):")
        for user, email in updated_owners:
            self.stdout.write(f"  {user.username} -> {email}")
            if apply_changes:
                user.email = email
                user.save(update_fields=['email'])

        self.stdout.write(f"\nMembers to update ({len(updated_members)}):")
        for user, email in updated_members:
            self.stdout.write(f"  {user.username} -> {email}")
            if apply_changes:
                user.email = email
                user.save(update_fields=['email'])

        self.stdout.write(self.style.ERROR(
            f"\nSkipped — looked invalid, NOT written ({len(skipped_invalid)}):"
        ))
        for user, bad_value in skipped_invalid:
            self.stdout.write(f"  {user.username} -> '{bad_value}' (rejected)")

        self.stdout.write(f"\nStill no email available ({len(still_blank)}):")
        for user in still_blank:
            self.stdout.write(f"  {user.username}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Updated: {len(updated_owners) + len(updated_members)}. "
            f"Skipped as invalid: {len(skipped_invalid)}. "
            f"Still blank: {len(still_blank)}."
        ))
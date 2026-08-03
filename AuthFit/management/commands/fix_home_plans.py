from django.core.management.base import BaseCommand
from django.db.models import Count
from AuthFit.models import MembershipPlan  # adjust import

class Command(BaseCommand):
    help = "Cap show_on_home plans to 3 per gym (fixes stale over-selected data)"

    def handle(self, *args, **options):
        self.stdout.write("Command started...")
        gym_ids = (
            MembershipPlan.objects.filter(show_on_home=True)
            .values('gym_id').annotate(c=Count('id')).filter(c__gt=3)
            .values_list('gym_id', flat=True)
        )
        for gym_id in gym_ids:
            keep_ids = (
                MembershipPlan.objects.filter(gym_id=gym_id, show_on_home=True)
                .order_by('price').values_list('pk', flat=True)[:3]
            )
            updated = MembershipPlan.objects.filter(gym_id=gym_id, show_on_home=True)\
                .exclude(pk__in=list(keep_ids)).update(show_on_home=False)
            self.stdout.write(f"Gym {gym_id}: reset {updated} plans")
# Gym/tasks.py

from django.utils import timezone
from .models import Gym

def deactivate_expired_gyms():
    """
    Run daily. Marks gyms as inactive when subscription_end passes.
    Call via cron-job.org hitting /internal/run-gym-expiry-check/
    """
    today = timezone.now().date()
    expired = Gym.objects.filter(
        active=True,
        subscription_end__lt=today
    )
    count = expired.update(active=False)
    return f"Deactivated {count} gyms"
"""
communications/signals.py

Mirrors announcements/models.py's own cache-invalidation pattern (see
clear_announcement_caches there) — kept in a dedicated signals.py here per
this app's directory_structure rather than inline in models.py.
"""

import logging

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Communication, CommunicationCampaign, CommunicationDeliveryLog, CommunicationSponsor

logger = logging.getLogger(__name__)

DASHBOARD_CACHE_KEY = 'communications_dashboard_stats'  # kept in sync with views.py's own constant


@receiver([post_save, post_delete], sender=Communication)
def clear_communication_caches(sender, instance, **kwargs):
    cache.delete("communications_active_all")
    cache.delete(f"communications_type_{instance.type}")
    cache.delete(DASHBOARD_CACHE_KEY)


@receiver([post_save, post_delete], sender=CommunicationCampaign)
@receiver([post_save, post_delete], sender=CommunicationSponsor)
def clear_dashboard_cache_on_campaign_sponsor_change(sender, instance, **kwargs):
    cache.delete(DASHBOARD_CACHE_KEY)


@receiver(post_save, sender=CommunicationDeliveryLog)
def clear_dashboard_cache_on_delivery(sender, instance, **kwargs):
    # Delivery outcomes feed the dashboard's push_delivered/push_failed
    # counts too — a fresh dispatch shouldn't wait out the TTL to show up.
    cache.delete(DASHBOARD_CACHE_KEY)
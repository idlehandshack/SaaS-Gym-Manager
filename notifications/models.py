# notification/models.py

from django.db import models
from django.contrib.auth.models import User


class WebPushSubscription(models.Model):
    """
    Stores a browser/PWA push subscription for web push notifications.
    One user can have multiple subscriptions (phone PWA + laptop + tablet).

    No gym FK — subscriptions are tied to a browser session, not a tenant.
    Gym scoping is handled at query time via user → StaffProfile → gym.

    Stale subscriptions (HTTP 410 from push service) are marked
    active=False rather than deleted, so we keep an audit trail.
    """
    user      = models.ForeignKey(
                    User,
                    on_delete=models.CASCADE,
                    related_name='web_push_subs',
                    db_index=True,
                )
    endpoint  = models.TextField(unique=True)
    p256dh    = models.TextField()    # browser public key
    auth      = models.TextField()    # auth secret
    active    = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used  = models.DateTimeField(null=True, blank=True)  # updated on successful push

    def __str__(self):
        status = 'active' if self.active else 'expired'
        return f"{self.user.username} — {self.endpoint[:50]} ({status})"

    class Meta:
        verbose_name        = 'Web Push Subscription'
        verbose_name_plural = 'Web Push Subscriptions'
        ordering            = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'active']),  # common query pattern
        ]
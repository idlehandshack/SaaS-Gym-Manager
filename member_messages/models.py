# member_messages/models.py
"""
Private one-to-one messaging between gym staff (Owner/Receptionist) and a
single member. Completely independent of the Announcement module — no
shared models, no shared views. Only the notification *infrastructure*
(UserDevice, send_push_to_tokens, send_web_push) is reused, from services.py.
"""

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from Gym.models import Gym


class MemberMessageQuerySet(models.QuerySet):
    def active(self):
        return self.filter(deleted_at__isnull=True)

    def for_gym(self, gym):
        return self.filter(gym=gym)

    def for_member(self, member):
        return self.filter(member=member)

    def unread(self):
        return self.filter(is_read=False)

    def inbox_visible(self):
        return self.filter(save_inbox=True)

    def popup_visible(self):
        return self.filter(show_popup=True)


class MemberMessageManager(models.Manager):
    """Default manager — soft-deleted rows are invisible everywhere by default."""

    def get_queryset(self):
        return MemberMessageQuerySet(self.model, using=self._db).active()


class MemberMessage(models.Model):
    PRIORITY_NORMAL = 'normal'
    PRIORITY_IMPORTANT = 'important'
    PRIORITY_URGENT = 'urgent'
    PRIORITY_CHOICES = [
        (PRIORITY_NORMAL, 'Normal'),
        (PRIORITY_IMPORTANT, 'Important'),
        (PRIORITY_URGENT, 'Urgent'),
    ]

    gym = models.ForeignKey(
        Gym, on_delete=models.CASCADE, db_index=True,
        related_name='member_messages',
    )
    member = models.ForeignKey(
        User, on_delete=models.CASCADE, db_index=True,
        related_name='received_messages',
        help_text="The single member this message is addressed to.",
    )

    title = models.CharField(max_length=200)
    message = models.TextField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)

    show_popup = models.BooleanField(default=True)
    send_push = models.BooleanField(default=True)
    save_inbox = models.BooleanField(default=True)

    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
        help_text="Staff user (Owner/Receptionist) who sent this message.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = MemberMessageManager()
    all_objects = models.Manager()  # includes soft-deleted rows — audit/admin use only

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['gym']),
            models.Index(fields=['member']),
            models.Index(fields=['is_read']),
            models.Index(fields=['created_at']),
            models.Index(fields=['gym', 'member', 'is_read']),
        ]
        verbose_name = 'Member Message'
        verbose_name_plural = 'Member Messages'

    def __str__(self):
        return f"{self.title} → {self.member.username} ({self.gym.gym_code})"

    # ── Helpers ───────────────────────────────────────────────────────────
    @property
    def status_display(self):
        return "Read" if self.is_read else "Unread"

    def mark_read(self):
        """Idempotent — returns False if it was already read."""
        if self.is_read:
            return False
        self.is_read = True
        self.read_at = timezone.now()
        self.save(update_fields=['is_read', 'read_at', 'updated_at'])
        return True

    def soft_delete(self):
        self.deleted_at = timezone.now()
        self.save(update_fields=['deleted_at', 'updated_at'])

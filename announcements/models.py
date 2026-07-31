"""
announcements/models.py

Two models, following the same tenant-isolation pattern used across the
codebase (gym FK + GymManager from Gym.mixins, mirroring Shop/AuthFit models).

Announcement
    One row = one notice. Belongs to exactly one Gym. Every display/behaviour
    flag (popup, banner, web, mobile, push) is a plain boolean so the owner
    can compose exactly the channels they want per-announcement.

AnnouncementRead
    One row per (announcement, user) — created lazily the first time a
    member's read/dismiss state needs to be recorded. Doubles as the delivery
    log for popup "already seen" checks and as the source for read-tracking
    analytics (owner_dashboard.analytics in the spec).
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from cloudinary.models import CloudinaryField

from Gym.models import Gym
from Gym.mixins import GymManager
from AuthFit.models import Enrollment, MembershipPlan
from AuthFit.models import Trainer as AuthFitTrainer  # AuthFit.Trainer, not Gym staff

logger = logging.getLogger(__name__)

EXPIRING_SOON_WINDOW_DAYS = 3  # "Members Expiring in 3 Days" audience window


class Announcement(models.Model):

    class Category(models.TextChoices):
        OFFER            = 'offer',            'Offer'
        INSTRUCTION      = 'instruction',       'Instruction'
        ALERT            = 'alert',             'Alert'
        HOLIDAY          = 'holiday',           'Gym Holiday'
        NEW_PLAN         = 'new_plan',          'New Plan'
        EVENT            = 'event',             'Event'
        MAINTENANCE      = 'maintenance',       'Maintenance'
        PAYMENT_REMINDER = 'payment_reminder',  'Payment Reminder'
        TRAINER_UPDATE   = 'trainer_update',    'Trainer Update'
        GENERAL          = 'general',           'General Notice'

    class Priority(models.TextChoices):
        HIGH   = 'high',   'High'
        MEDIUM = 'medium', 'Medium'
        LOW    = 'low',    'Low'

    class Audience(models.TextChoices):
        ALL              = 'all',              'All Members'
        ACTIVE           = 'active',           'Only Active Members'
        EXPIRED          = 'expired',          'Expired Members'
        EXPIRING_SOON    = 'expiring_soon',    'Members Expiring in 3 Days'
        PLAN             = 'plan',             'Members of Specific Plan'
        TRAINER          = 'trainer',          'Members of Specific Trainer'
        SPECIFIC         = 'specific',         'Specific Members'

    # ── Ownership / tenant isolation ────────────────────────────────────
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True, related_name='announcements')
    objects = GymManager()

    # ── Content ──────────────────────────────────────────────────────────
    title       = models.CharField(max_length=150)
    description = models.TextField(help_text="Rich text (HTML from the editor). Sanitized on save.")
    announcement_type = models.CharField(max_length=20, choices=Category.choices, default=Category.GENERAL)
    priority    = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM, db_index=True)

    image      = CloudinaryField('announcement_image', null=True, blank=True)
    attachment = CloudinaryField(
        'announcement_attachment', null=True, blank=True, resource_type='raw',
    )
    external_link = models.URLField(blank=True)

    # ── Scheduling ───────────────────────────────────────────────────────
    publish_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_active  = models.BooleanField(default=True, db_index=True)

    # ── Display channels ─────────────────────────────────────────────────
    show_popup    = models.BooleanField(default=False)
    show_banner   = models.BooleanField(default=False)
    show_web      = models.BooleanField(default=True)
    show_mobile   = models.BooleanField(default=True)
    send_push     = models.BooleanField(default=False)
    require_read  = models.BooleanField(
        default=False,
        help_text="If set, High-priority popups stay until the member explicitly presses Mark as Read "
                   "(Dismiss alone does not satisfy this).",
    )
    pin_home = models.BooleanField(default=False)

    # ── Targeting ────────────────────────────────────────────────────────
    target_audience = models.CharField(max_length=20, choices=Audience.choices, default=Audience.ALL)
    target_plan     = models.ForeignKey(MembershipPlan, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    target_trainer  = models.ForeignKey(AuthFitTrainer, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    target_members  = models.ManyToManyField(User, blank=True, related_name='targeted_announcements')

    # ── Push delivery bookkeeping (analytics.push_sent_count) ──────────────
    push_sent_at    = models.DateTimeField(null=True, blank=True)
    push_sent_count = models.PositiveIntegerField(default=0)
    view_count      = models.PositiveIntegerField(
        default=0,
        help_text="Incremented once per member the first time it enters their home/center feed. "
                   "Best-effort counter, not a unique-visitor guarantee.",
    )

    # ── Audit ────────────────────────────────────────────────────────────
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-pin_home', '-publish_at']
        indexes = [
            models.Index(fields=['gym', 'is_active', 'publish_at']),
            models.Index(fields=['gym', 'announcement_type']),
            models.Index(fields=['gym', 'priority']),
        ]
        verbose_name = 'Announcement'
        verbose_name_plural = 'Announcements'

    def __str__(self):
        return f"[{self.gym.gym_code}] {self.title}"

    # ── State helpers ────────────────────────────────────────────────────
    @property
    def is_published(self) -> bool:
        return self.is_active and self.publish_at <= timezone.now()

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and timezone.now() > self.expires_at)

    @property
    def is_live(self) -> bool:
        """True only when it should actually be visible to members right now."""
        return self.is_published and not self.is_expired

    def get_absolute_url(self):
        return reverse('announcement_center') + f'?open={self.pk}'

    # ── Targeting resolution ─────────────────────────────────────────────
    def audience_enrollment_queryset(self):
        """
        Resolve target_audience into an Enrollment queryset scoped to this
        gym. Used by both the popup/home feed (single user check) and by
        the push-notification fan-out (bulk).
        """
        qs = Enrollment.objects.filter(gym=self.gym, is_deleted=False, user__isnull=False)

        if self.target_audience == self.Audience.ACTIVE:
            qs = qs.filter(is_active=True)
        elif self.target_audience == self.Audience.EXPIRED:
            qs = qs.filter(DueDate__lt=timezone.localdate())
        elif self.target_audience == self.Audience.EXPIRING_SOON:
            today = timezone.localdate()
            qs = qs.filter(DueDate__gte=today, DueDate__lte=today + timedelta(days=EXPIRING_SOON_WINDOW_DAYS))
        elif self.target_audience == self.Audience.PLAN and self.target_plan_id:
            qs = qs.filter(selectPlan=self.target_plan_id)
        elif self.target_audience == self.Audience.TRAINER and self.target_trainer_id:
            qs = qs.filter(trainer=self.target_trainer_id)
        elif self.target_audience == self.Audience.SPECIFIC:
            member_ids = list(self.target_members.values_list('id', flat=True))
            qs = qs.filter(user_id__in=member_ids)
        # Audience.ALL -> no extra filter

        return qs

    def is_targeted_at(self, user) -> bool:
        """Single-user check used for the home popup / feed visibility gate."""
        if self.target_audience == self.Audience.ALL:
            return True
        if self.target_audience == self.Audience.SPECIFIC:
            return self.target_members.filter(pk=user.pk).exists()
        return self.audience_enrollment_queryset().filter(user_id=user.pk).exists()


class AnnouncementRead(models.Model):

    class DeviceType(models.TextChoices):
        WEB     = 'web',     'Web'
        MOBILE  = 'mobile',  'Mobile App'
        UNKNOWN = 'unknown', 'Unknown'

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name='reads')
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='announcement_reads')

    read_at      = models.DateTimeField(null=True, blank=True)
    dismissed    = models.BooleanField(default=False)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    device_type  = models.CharField(max_length=10, choices=DeviceType.choices, default=DeviceType.UNKNOWN)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['announcement', 'user'], name='unique_announcement_read_per_user'),
        ]
        indexes = [
            models.Index(fields=['announcement', 'read_at']),
            models.Index(fields=['user', 'announcement']),
        ]
        verbose_name = 'Announcement Read'
        verbose_name_plural = 'Announcement Reads'

    def __str__(self):
        state = 'read' if self.read_at else ('dismissed' if self.dismissed else 'seen')
        return f"{self.user.username} — {self.announcement.title} ({state})"

    def mark_read(self, device_type=DeviceType.UNKNOWN):
        self.read_at = timezone.now()
        if device_type:
            self.device_type = device_type
        self.save(update_fields=['read_at', 'device_type'])

    def mark_dismissed(self, device_type=DeviceType.UNKNOWN):
        self.dismissed = True
        self.dismissed_at = timezone.now()
        if device_type:
            self.device_type = device_type
        self.save(update_fields=['dismissed', 'dismissed_at', 'device_type'])


# ── Cache invalidation ──────────────────────────────────────────────────────
# Mirrors the cache-clearing pattern used for GymNotification / MembershipPlan
# in AuthFit/models.py — home page / popup queries are cached per gym.

@receiver([post_save, post_delete], sender=Announcement)
def clear_announcement_caches(sender, instance, **kwargs):
    cache.delete(f"announcements_active_{instance.gym_id}")
    cache.delete(f"announcements_popup_{instance.gym_id}")
    cache.delete(f"announcements_banner_{instance.gym_id}")
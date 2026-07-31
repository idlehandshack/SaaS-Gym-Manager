"""
communications/models.py

Brand-new app, isolated from `announcements`. Nothing here imports from or
writes to any `announcements.*` model — see the module docstring in
services.py for the reuse boundary (we call into Shop.notifications /
notifications.utils, the same shared senders `announcements` already uses,
but we never touch announcements' own tables).

Communication
    One row = one platform-wide item (announcement / promo / ad / campaign /
    release / maintenance notice / survey / event / offer). Unlike
    announcements.Announcement, this is NOT gym-scoped — it's created by the
    Super Admin and fanned out to whatever audience CommunicationAudience
    resolves to, which may span many gyms at once.

CommunicationAudience
    One-to-one with Communication. Holds every possible targeting dimension
    from the spec. Resolution logic lives in services.AudienceResolver, not
    here — this model is just the stored configuration.

CommunicationDeliveryLog
    One row per (communication, recipient, channel) delivery attempt. Powers
    the analytics dashboard (sent/delivered/failed/opened/clicked/dismissed/
    read, open rate, click rate, etc).

CommunicationSponsor / CommunicationCampaign
    Scaffolding for the "future_sponsor_system" section of the spec. Kept
    deliberately thin — enough that a Campaign can already own a set of
    Communications without any future migration needing to touch the core
    Communication model (success_criteria: "Sponsor campaigns can be added
    without changing the core architecture").
"""

import logging

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from cloudinary.models import CloudinaryField

from Gym.models import Gym, SubscriptionPlan
from AuthFit.models import MembershipPlan

logger = logging.getLogger(__name__)

EXPIRING_SOON_WINDOW_DAYS = 3  # mirrors announcements/models.py's own constant


# ──────────────────────────────────────────────────────────────────────────
# Soft-delete manager for Communication — mirrors Enrollment's own
# is_deleted convention elsewhere in the codebase, rather than hard
# deleting rows that CommunicationAuditLog / CommunicationDeliveryLog still
# reference. `Communication.objects` (default) hides deleted rows
# everywhere automatically; `Communication.all_objects` is the escape
# hatch for admin/audit screens that need to see them.
# ──────────────────────────────────────────────────────────────────────────

class CommunicationQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(is_deleted=False)


class CommunicationManager(models.Manager):
    def get_queryset(self):
        return CommunicationQuerySet(self.model, using=self._db).filter(is_deleted=False)


class CommunicationAllObjectsManager(models.Manager):
    def get_queryset(self):
        return CommunicationQuerySet(self.model, using=self._db)


# ──────────────────────────────────────────────────────────────────────────
# Sponsor / Campaign (future_sponsor_system) — thin scaffold, wired in now
# so nothing about Communication itself has to change later.
# ──────────────────────────────────────────────────────────────────────────

class CommunicationSponsor(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'

    name = models.CharField(max_length=150)
    logo = CloudinaryField('sponsor_logo', null=True, blank=True)
    website = models.URLField(blank=True)
    contact_person = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Sponsor'
        verbose_name_plural = 'Sponsors'

    def __str__(self):
        return self.name


class CommunicationCampaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'
        COMPLETED = 'completed', 'Completed'

    name = models.CharField(max_length=150)
    sponsor = models.ForeignKey(
        CommunicationSponsor, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='campaigns',
    )
    budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Campaign'
        verbose_name_plural = 'Campaigns'

    def __str__(self):
        return self.name


# ──────────────────────────────────────────────────────────────────────────
# Communication — the core item
# ──────────────────────────────────────────────────────────────────────────

class Communication(models.Model):

    class Type(models.TextChoices):
        ANNOUNCEMENT = 'announcement', 'Platform Announcement'
        NOTIFICATION = 'notification', 'Notification'
        PROMOTION = 'promotion', 'Promotion'
        ADVERTISEMENT = 'advertisement', 'Advertisement'
        CAMPAIGN = 'campaign', 'Campaign'
        RELEASE = 'release', 'Product Release'
        MAINTENANCE = 'maintenance', 'Maintenance Notice'
        SURVEY = 'survey', 'Survey'
        EVENT = 'event', 'Event'
        OFFER = 'offer', 'Offer'

    class Priority(models.TextChoices):
        CRITICAL = 'critical', 'Critical'
        HIGH = 'high', 'High'
        MEDIUM = 'medium', 'Medium'
        LOW = 'low', 'Low'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SCHEDULED = 'scheduled', 'Scheduled'
        PUBLISHED = 'published', 'Published'
        EXPIRED = 'expired', 'Expired'
        CANCELLED = 'cancelled', 'Cancelled'

    # ── Content ──────────────────────────────────────────────────────────
    title = models.CharField(max_length=150)
    description = models.TextField(help_text="Rich text (HTML from the editor). Sanitized on save.")
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.ANNOUNCEMENT, db_index=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)

    image = CloudinaryField('communication_image', null=True, blank=True)
    attachment = CloudinaryField('communication_attachment', null=True, blank=True, resource_type='raw')
    external_link = models.URLField(blank=True)

    # ── Scheduling ───────────────────────────────────────────────────────
    publish_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    # ── Channel toggles ──────────────────────────────────────────────────
    # push/web_push/pwa reuse existing senders today; email/whatsapp/sms are
    # accepted here (per success_criteria: "future channels can be added
    # without redesigning the database") but the dispatcher no-ops them
    # until Shop/notifications equivalents exist for those channels.
    channel_push = models.BooleanField(default=True, help_text="Firebase Cloud Messaging (mobile).")
    channel_web_push = models.BooleanField(default=True, help_text="Browser / PWA web push.")
    channel_pwa = models.BooleanField(default=True, help_text="PWA in-app notification center.")
    channel_email = models.BooleanField(default=False, help_text="Future — no sender implemented yet.")
    channel_whatsapp = models.BooleanField(default=False, help_text="Future — no sender implemented yet.")
    channel_sms = models.BooleanField(default=False, help_text="Future — no sender implemented yet.")

    show_popup = models.BooleanField(default=False)
    require_read = models.BooleanField(
        default=False,
        help_text="If set, a popup stays until the recipient explicitly presses Mark as "
                   "Read — Dismiss alone does not satisfy this. Mirrors "
                   "announcements.Announcement.require_read.",
    )
    show_banner = models.BooleanField(default=False)

    class BannerPlacement(models.TextChoices):
        HOME = 'home', 'Home Banner'
        DASHBOARD = 'dashboard', 'Dashboard Banner'
        CAROUSEL = 'carousel', 'Carousel'
        TOP = 'top', 'Top Banner'
        BOTTOM = 'bottom', 'Bottom Banner'

    banner_placement = models.CharField(
        max_length=20, choices=BannerPlacement.choices, default=BannerPlacement.HOME, blank=True,
        help_text="Only used when show_banner is True.",
    )
    show_notification_center = models.BooleanField(default=True)

    class DeepLink(models.TextChoices):
        NONE = '', 'None — opens Communication Center'
        ATTENDANCE = 'attendance', 'Attendance'
        BILLING = 'billing', 'Billing'
        WORKOUT = 'workout', 'Workout'
        MEMBER_PROFILE = 'member_profile', 'Member Profile'
        REPORTS = 'reports', 'Reports'
        SHOP = 'shop', 'Shop'
        WEBSITE = 'website', 'Website (uses External Link)'
        YOUTUBE = 'youtube', 'YouTube (uses External Link)'
        INSTAGRAM = 'instagram', 'Instagram (uses External Link)'
        EXTERNAL_URL = 'external_url', 'Other External URL (uses External Link)'

    deep_link_type = models.CharField(
        max_length=20, choices=DeepLink.choices, blank=True, default=DeepLink.NONE,
        help_text="Where tapping this communication should take the recipient. Internal "
                   "screens route the mobile app the same way existing push payloads "
                   "already do (e.g. 'screen': 'Profile'); the *_URL types use External Link.",
    )

    # ── Sponsor/campaign linkage (future_sponsor_system) ────────────────
    campaign = models.ForeignKey(
        CommunicationCampaign, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='communications',
    )

    # ── Delivery bookkeeping (analytics) ─────────────────────────────────
    dispatched_at = models.DateTimeField(null=True, blank=True)
    dispatch_success_count = models.PositiveIntegerField(default=0)
    dispatch_failure_count = models.PositiveIntegerField(default=0)
    total_impressions = models.PositiveIntegerField(
        default=0,
        help_text="Incremented once per recipient the first time it's shown "
                   "in their notification center / home feed.",
    )
    is_dispatching = models.BooleanField(
        default=False, db_index=True,
        help_text="Transient guard held for the duration of one dispatch call. "
                   "Prevents a double 'Publish Now' click or an overlapping cron "
                   "tick from sending the same communication twice — see "
                   "services.dispatch_communication's atomic claim/release.",
    )

    # ── Audit ────────────────────────────────────────────────────────────
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    published_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    cancelled_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Soft delete ──────────────────────────────────────────────────────
    # Mirrors AuthFit.Enrollment's is_deleted/deleted_at/deleted_by
    # convention. `objects` (default manager) hides these automatically;
    # use `all_objects` for anything that must see deleted rows (audit).
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = CommunicationManager()
    all_objects = CommunicationAllObjectsManager()

    class Meta:
        ordering = ['-publish_at']
        indexes = [
            models.Index(fields=['status', 'publish_at']),
            models.Index(fields=['type']),
            models.Index(fields=['priority']),
        ]
        verbose_name = 'Communication'
        verbose_name_plural = 'Communications'

    def __str__(self):
        return f"[{self.get_type_display()}] {self.title}"

    # ── State helpers — mirror announcements.Announcement's own properties
    @property
    def is_published(self) -> bool:
        return self.is_active and self.status == self.Status.PUBLISHED and self.publish_at <= timezone.now()

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and timezone.now() > self.expires_at)

    @property
    def is_live(self) -> bool:
        return self.is_published and not self.is_expired

    # Mirrors the "screen" values Shop/notifications.py and
    # AuthFit/notifications.py already put in their FCM `data` payloads
    # (e.g. "screen": "Profile", "screen": "AdminOrders") — reusing that
    # same convention rather than inventing a second routing scheme.
    _MOBILE_SCREEN_MAP = {
        DeepLink.ATTENDANCE: 'Attendance',
        DeepLink.BILLING: 'Billing',
        DeepLink.WORKOUT: 'Workout',
        DeepLink.MEMBER_PROFILE: 'Profile',
        DeepLink.REPORTS: 'Reports',
        DeepLink.SHOP: 'Shop',
    }
    _URL_DEEP_LINKS = {DeepLink.WEBSITE, DeepLink.YOUTUBE, DeepLink.INSTAGRAM, DeepLink.EXTERNAL_URL}

    def get_deep_link(self) -> dict:
        """
        Returns {"kind": "screen"|"url"|"center", "value": ...}.
        - "screen"  -> internal mobile app route; value is the screen name.
        - "url"     -> external_link should be opened (website/YouTube/Instagram/other).
        - "center"  -> no explicit target configured; land on the Communication Center.
        """
        screen = self._MOBILE_SCREEN_MAP.get(self.deep_link_type)
        if screen:
            return {"kind": "screen", "value": screen}
        if self.deep_link_type in self._URL_DEEP_LINKS and self.external_link:
            return {"kind": "url", "value": self.external_link}
        return {"kind": "center", "value": None}

    def mark_dispatched(self, success_count: int, failure_count: int) -> None:
        self.dispatched_at = timezone.now()
        self.dispatch_success_count = success_count
        self.dispatch_failure_count = failure_count
        self.status = self.Status.PUBLISHED
        self.save(update_fields=[
            'dispatched_at', 'dispatch_success_count', 'dispatch_failure_count', 'status',
        ])

    def soft_delete(self, user=None) -> None:
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.is_active = False
        self.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'is_active'])

    def restore(self) -> None:
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])


class CommunicationAudience(models.Model):
    """
    Targeting configuration for one Communication. Resolution into an
    actual list of (user, gym) recipients happens in
    services.AudienceResolver — this model only stores what the Super
    Admin picked.
    """

    class Scope(models.TextChoices):
        EVERYONE = 'everyone', 'Everyone'
        ALL_MEMBERS = 'all_members', 'All Members'
        ALL_STAFF = 'all_staff', 'All Staff'
        ALL_OWNERS = 'all_owners', 'All Gym Owners'
        ALL_RECEPTIONISTS = 'all_receptionists', 'All Receptionists'
        ALL_TRAINERS = 'all_trainers', 'All Trainers'
        SPECIFIC_GYM = 'specific_gym', 'Specific Gym(s)'
        SPECIFIC_PLAN = 'specific_plan', 'Members of Specific Plan (by name)'
        SUBSCRIPTION_PLAN = 'subscription_plan', 'Gyms on Specific SaaS Plan'
        SPECIFIC_CITY = 'specific_city', 'Specific City'
        SPECIFIC_STATE = 'specific_state', 'Specific State'
        SPECIFIC_COUNTRY = 'specific_country', 'Specific Country'
        SPECIFIC_MEMBERS = 'specific_members', 'Specific Members'
        SPECIFIC_STAFF = 'specific_staff', 'Specific Staff'
        ACTIVE_MEMBERS = 'active_members', 'Active Members'
        EXPIRED_MEMBERS = 'expired_members', 'Expired Members'
        EXPIRING_MEMBERS = 'expiring_members', 'Expiring Members'

    communication = models.OneToOneField(Communication, on_delete=models.CASCADE, related_name='audience')
    scope = models.CharField(max_length=30, choices=Scope.choices, default=Scope.EVERYONE, db_index=True)

    # SPECIFIC_GYM / narrows any of the *_MEMBERS / *_STAFF scopes to these
    # gyms only, when non-empty (e.g. "active_members" + gyms=[X,Y] ->
    # active members of X and Y only, not the whole platform).
    gyms = models.ManyToManyField(Gym, blank=True, related_name='+')

    # SPECIFIC_PLAN — MembershipPlan is gym-scoped (each gym creates its own
    # rows even for a plan called "Gold"), so cross-gym targeting is done by
    # name match rather than FK. `plans` lets the admin optionally also pick
    # specific concrete rows (e.g. "Gold @ FitZone" only).
    plan_name_filter = models.CharField(
        max_length=100, blank=True,
        help_text="Case-insensitive match against AuthFit.MembershipPlan.plan, "
                   "e.g. 'Gold' matches every gym's Gold-named plan.",
    )
    plans = models.ManyToManyField(MembershipPlan, blank=True, related_name='+')

    # SUBSCRIPTION_PLAN — targets gym owners of gyms on a given SaaS tier.
    subscription_plans = models.ManyToManyField(SubscriptionPlan, blank=True, related_name='+')

    # SPECIFIC_CITY — Gym.city exists today; Gym has no state/country field
    # yet, so SPECIFIC_STATE / SPECIFIC_COUNTRY are accepted here (per the
    # "no redesigning the database later" goal) but AudienceResolver
    # currently resolves them to an empty set and logs a warning until
    # those fields are added to Gym.
    cities = models.JSONField(default=list, blank=True, help_text="List of city names, matched case-insensitively against Gym.city.")
    states = models.JSONField(default=list, blank=True, help_text="Not yet resolvable — Gym has no state field.")
    countries = models.JSONField(default=list, blank=True, help_text="Not yet resolvable — Gym has no country field.")

    specific_members = models.ManyToManyField(User, blank=True, related_name='targeted_communications_as_member')
    specific_staff = models.ManyToManyField(User, blank=True, related_name='targeted_communications_as_staff')

    class Meta:
        verbose_name = 'Communication Audience'
        verbose_name_plural = 'Communication Audiences'

    def __str__(self):
        return f"{self.communication.title} → {self.get_scope_display()}"


class CommunicationDeliveryLog(models.Model):
    """
    One row per (communication, recipient, channel) delivery attempt.
    Distinct table from announcements' own tracking (that app has none —
    it uses AnnouncementRead) and from AuthFit's PushNotificationLog /
    WhatsAppMessageLog, since those are per-gym business-event logs and
    this is the platform-wide communications equivalent.
    """

    CHANNEL_CHOICES = [
        ('fcm', 'FCM (Mobile App)'),
        ('web_push', 'Web Push (Browser/PWA)'),
        ('email', 'Email'),
        ('whatsapp', 'WhatsApp'),
        ('sms', 'SMS'),
    ]
    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('failed', 'Failed'),
        ('opened', 'Opened'),
        ('clicked', 'Clicked'),
        ('dismissed', 'Dismissed'),
        ('read', 'Read'),
    ]

    communication = models.ForeignKey(Communication, on_delete=models.CASCADE, related_name='delivery_logs')

    # Nullable + SET_NULL, matching WhatsAppMessageLog/PushNotificationLog's
    # own "the log outlives the thing it's about" philosophy.
    gym = models.ForeignKey(Gym, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    recipient = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='sent', db_index=True)

    # Idempotency — same pattern as WhatsAppMessageLog.deduplication_key,
    # blank for best-effort push/web-push sends (no natural retry key
    # today), populated once email/whatsapp/sms adapters exist.
    deduplication_key = models.CharField(max_length=255, blank=True, default='', db_index=True)

    delivered_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    clicked_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['communication', 'channel']),
            models.Index(fields=['communication', 'status']),
            models.Index(fields=['gym', 'created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['communication', 'deduplication_key'],
                condition=~models.Q(deduplication_key=''),
                name='unique_communication_dedup_key',
            )
        ]
        verbose_name = 'Communication Delivery Log'
        verbose_name_plural = 'Communication Delivery Logs'

    def __str__(self):
        return f"{self.communication_id} → {self.recipient_id} [{self.channel}] {self.status}"


class CommunicationAuditLog(models.Model):
    """
    Append-only actor trail for Communication actions. Distinct from
    CommunicationDeliveryLog (that's per-recipient delivery outcomes, this
    is per-admin-action bookkeeping) and modeled after Gym.AuditLog /
    AuthFit.EnrollmentDeletionLog's own "permanent, delete() raises"
    pattern — kept local to this app rather than writing into Gym's
    AuditLog table, since that model's ACTION_CHOICES is Gym business logic
    we're not allowed to touch.
    """
    ACTION_CHOICES = [
        ('created', 'Created'),
        ('updated', 'Updated'),
        ('published', 'Published'),
        ('cancelled', 'Cancelled'),
        ('deleted', 'Deleted'),
        ('restored', 'Restored'),
        ('bulk_action', 'Bulk Action'),
    ]

    # Nullable + SET_NULL + a snapshot title, same "the log outlives the
    # thing it's about" philosophy as CommunicationDeliveryLog.
    communication = models.ForeignKey(
        Communication, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs',
    )
    communication_title_snapshot = models.CharField(max_length=150, blank=True)

    action = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    detail = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['communication', 'created_at']),
            models.Index(fields=['action']),
        ]
        verbose_name = 'Communication Audit Log'
        verbose_name_plural = 'Communication Audit Logs'

    def delete(self, *args, **kwargs):
        raise PermissionError("CommunicationAuditLog rows are a permanent audit trail and cannot be deleted.")

    def __str__(self):
        return f"{self.communication_title_snapshot} — {self.get_action_display()} by {self.actor} @ {self.created_at:%d %b %Y %H:%M}"


def log_communication_action(communication, action, actor=None, detail='') -> None:
    """
    Single write path for CommunicationAuditLog — mirrors
    AuthFit.audit.log_action()'s role for Gym.AuditLog. Wrapped in its own
    try/except so a logging failure can never take down the actual action
    it's recording, matching AuthFit/notifications.py's
    _log_push_notification isolation philosophy.
    """
    try:
        CommunicationAuditLog.objects.create(
            communication=communication,
            communication_title_snapshot=communication.title[:150],
            action=action,
            actor=actor,
            detail=detail[:255],
        )
    except Exception:
        logger.exception(
            "log_communication_action: failed to write audit row communication=%s action=%s",
            getattr(communication, 'id', None), action,
        )
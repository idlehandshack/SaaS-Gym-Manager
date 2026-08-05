"""
Gym/models.py
--------------
Core multi-tenant model.  Every other app's models carry a FK to Gym.
"""

import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from cloudinary.models import CloudinaryField
from django.core.cache import cache
from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from Gym.fields import EncryptedTextField
from Gym.mixins import GymManager
from django.core.validators import RegexValidator
from datetime import time as _time


E164_PHONE_VALIDATOR = RegexValidator(
    regex=r'^\+[1-9]\d{7,14}$',
    message="Phone number must be in E.164 format, e.g. +919876543210 or +14155552671.",
)
# ──────────────────────────────────────────────────────────────────────────────
# Subscription Plans (SaaS tiers defined by the software owner)
# ──────────────────────────────────────────────────────────────────────────────
class EquipmentBrand(models.Model):
    name = models.CharField(max_length=100, unique=True)
    # CHANGED — was: models.ImageField(upload_to='equipment_brands/')
    logo = CloudinaryField('brand_logo', null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
 
    class Meta:
        ordering = ['name']
        verbose_name = 'Equipment Brand'
        verbose_name_plural = 'Equipment Brands'
 
    def __str__(self):
        return self.name
 
 
class Service(models.Model):
    name = models.CharField(max_length=100, unique=True)
 
    # CHANGED — was: models.ImageField(upload_to='services/')
    image = CloudinaryField('service_image', null=True, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Controls display order everywhere this catalog is listed.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
 
    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = 'Service'
        verbose_name_plural = 'Services'
 
    def __str__(self):
        return self.name
    

def clear_gym_services_cache(sender, instance, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        cache.delete(f"gym_services_{instance.pk}")
 
 
@receiver([post_save, post_delete], sender=Service)
def clear_all_service_caches_on_catalog_change(sender, instance, **kwargs):
    """
    A catalog-level change (Super Admin edits/deletes/deactivates a
    service) can affect every gym that selected it, since the image
    and name are denormalized into each gym's cached homepage payload.
    """
    gym_ids = instance.gyms.values_list('pk', flat=True) if instance.pk else []
    for gym_id in gym_ids:
        cache.delete(f"gym_services_{gym_id}")

class SubscriptionPlan(models.Model):
    name            = models.CharField(max_length=60, unique=True) 
    price_monthly   = models.DecimalField(max_digits=10, decimal_places=2)
    member_limit    = models.PositiveIntegerField(default=100)
    trainer_limit   = models.PositiveIntegerField(default=5)
    feature_flags   = models.JSONField(default=dict, blank=True)   
    def __str__(self):
        return self.name
    class Meta:
        ordering = ['price_monthly']

# ──────────────────────────────────────────────────────────────────────────────
# Gym  (one row = one tenant)
# ──────────────────────────────────────────────────────────────────────────────
class Gym(models.Model):
    # Identity
    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    gym_name        = models.CharField(max_length=100)
    app_name = models.CharField(
        max_length=100, blank=True,
        help_text="Full application name shown during installation (e.g. 'Muscle Garage Fitness'). "
        "Falls back to gym_name if left blank."
        )
    app_short_name = models.CharField(
            max_length=30, blank=True,
            help_text="Short name shown below the app icon (e.g. 'MuscleGarage'). Falls back to gym_name if blank."
        )
    gym_code        = models.SlugField(max_length=20, unique=True, db_index=True)

    # Owner (1 User can own exactly 1 gym — use StaffProfile for multi-gym staff)
    owner           = models.OneToOneField(
                          User, on_delete=models.PROTECT,
                          related_name='owned_gym'
                      )

    # Subscription
    plan            = models.ForeignKey(
                          SubscriptionPlan, on_delete=models.PROTECT,
                          null=True, blank=True
                      )
    active          = models.BooleanField(default=True)
    subscription_start  = models.DateField(null=True, blank=True)
    subscription_end    = models.DateField(null=True, blank=True)

    # Limits (mirrored from plan but can be overridden per-gym)
    member_limit    = models.PositiveIntegerField(default=100)
    trainer_limit   = models.PositiveIntegerField(default=5)
    embedding_version = models.PositiveIntegerField(default=1)

    # ── White-label settings ──────────────────────────────────────────────
    logo = CloudinaryField(
        'gym_logo', null=True, blank=True,
        help_text="Primary square logo, recommended 512×512. Used as the 192×192 PWA icon."
    )
    favicon         = CloudinaryField('gym_favicon', null=True, blank=True)
    splash_logo     = CloudinaryField("gym_splash_logo", null=True, blank=True)
    contact_email   = models.EmailField(blank=True)
    contact_phone   = models.CharField(max_length=15, blank=True)
    whatsapp_number = models.CharField(max_length=15, blank=True)
    theme_color     = models.CharField(max_length=7, default='#007bff')  # hex
    receipt_footer  = models.TextField(blank=True)
    address         = models.TextField(blank=True)
    city            = models.CharField(max_length=60, blank=True)
    app_download_url = models.URLField(blank=True,help_text="APK download link or Play Store URL for this gym's app.")

    # ── Geo-fence (per gym) ───────────────────────────────────────────────
    latitude        = models.FloatField(default=0.0)
    longitude       = models.FloatField(default=0.0)
    radius_meters   = models.FloatField(default=100.0)
    map = models.TextField(blank=True)

    enable_store            = models.BooleanField(default=True,
        help_text="Supplement store & order management.")
    enable_attendance       = models.BooleanField(default=True,
        help_text="Geo-attendance and attendance analytics.")
    enable_geo_attendance   = models.BooleanField(default=False,
        help_text="GPS-based geo-fenced attendance specifically. Independent of"
                "enable_attendance (which just controls the Attendance module/UI "
                "visibility) and enable_face_recognition. Turn this off for gyms "
                "using only Face Recognition, a biometric device, or no GPS check-in "
                "at all — this stops all location JS, Service Worker polling, and "
                "geo API calls for that gym.")
    enable_face_recognition = models.BooleanField(default=True,
        help_text="Face recognition enrollment and auto check-in.")
    enable_trainers         = models.BooleanField(default=True,
        help_text="Trainer management module.")
    
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)
    upi_enabled = models.BooleanField(default=False)

    upi_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="e.g. yourgym@oksbi"
    )
    upi_display_name = models.CharField(
        max_length=120,
        blank=True,
        help_text="Name shown in the member's UPI app during payment."
    )

    upi_payment_note = models.CharField(
        max_length=120,
        default="Membership Payment",
        help_text="Transaction note (tn) attached to the UPI deep link."
    )
    equipment_brands = models.ManyToManyField(
        EquipmentBrand,
        blank=True,
        related_name='gyms',
    )
    services = models.ManyToManyField(
        Service,
        blank=True,
        related_name='gyms',
    )
    instagram_username = models.CharField(max_length=100, blank=True, default='')
    THEME_CHOICES = [
        ('default', 'Default'),
        ('blue',    'Blue'),
        ('black',   'Black'),
        ('pink',    'Pink'),
        ('green',   'Green'),
        ('red',     'Red'),
        ('custom',  'Custom'),
    ]
    theme = models.CharField(
        max_length=20,
        choices=THEME_CHOICES,
        default='default',
        help_text="Predefined UI color palette for this gym's dashboard/site."
    )
    MODE_CHOICES = [
        ('dark',  'Dark'),
        ('light', 'Light'),
    ]
    dashboard_mode = models.CharField(
        max_length=10,
        choices=MODE_CHOICES,
        default='dark',
        help_text="Dark or light background for the dashboard UI."
    )
    show_subscription_payment = models.BooleanField(
        default=False,
        help_text=(
            "When True, this gym's Owner/Receptionist see the 'Pay Subscription' "
            "button on their dashboard. Toggled only by the Super Admin from "
            "the All Gyms page. Has nothing to do with the existing payment "
            "gateway/upload system — this is purely a manual-payment flag."
        ),
    )
    last_expiry_reminder_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the last manual 'Send Expiry Reminder' click for this gym. "
                   "Used to enforce a once-per-calendar-day limit. Compared against "
                   "timezone.localdate() — not UTC date — so gyms see the limit reset at "
                   "their local midnight from the server's perspective.",
    )
    hidden_stat_cards = models.JSONField(default=list, blank=True)
    pending_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Amount still owed if Super Admin marked a renewal as 'not paid yet'."
    )
    @property
    def is_subscription_active(self):
        if not self.active:
            return False
        if self.subscription_end and timezone.now().date() > self.subscription_end:
            return False
        return True
    
    def clean(self):
        super().clean()
        if self.upi_enabled:
            from django.core.exceptions import ValidationError
            errors = {}
            if not self.upi_id.strip():
                errors['upi_id'] = "UPI ID cannot be empty when UPI is enabled."
            if not self.upi_display_name.strip():
                errors['upi_display_name'] = "Display Name cannot be empty when UPI is enabled."
            if errors:
                raise ValidationError(errors)
    @property
    def days_until_expiry(self):
        if self.subscription_end:
            return (self.subscription_end - timezone.now().date()).days
        return None

    def __str__(self):
        return f"{self.gym_name} ({self.gym_code})"
    @property
    def instagram_url(self):
        return f"https://instagram.com/{self.instagram_username}" if self.instagram_username else ''
    @property
    def social_links(self):
        links = {
            'instagram': self.instagram_url,
        }
        return {k: v for k, v in links.items() if v}
    class Meta:
        ordering  = ['gym_name']
        indexes   = [models.Index(fields=['gym_code'])]
        verbose_name        = 'Gym'
        verbose_name_plural = 'Gyms'

class SubscriptionPayment(models.Model):
    gym = models.ForeignKey(
        Gym, on_delete=models.CASCADE, related_name="subscription_payments"
    )
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.SET_NULL, null=True)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    payment_date = models.DateField()
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date"]

@receiver([post_save, post_delete], sender=Gym)
def clear_gym_logo_cache(sender, instance, **kwargs):
    cache.delete(f"gym_branding_{instance.pk}")
    cache.delete(f"manifest_{instance.pk}")

# ──────────────────────────────────────────────────────────────────────────────
# Staff Profile  (links a User to a Gym with a role)
# ──────────────────────────────────────────────────────────────────────────────
class StaffProfile(models.Model):
    ROLE_CHOICES = [
        ('super_admin',   'Super Admin'),    # software owner – set via is_superuser
        ('gym_owner',     'Gym Owner'),
        ('trainer',       'Trainer'),
        ('receptionist',  'Receptionist'),
    ]

    user    = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_profile')
    gym     = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name='staff', null=True, blank=True)
    role    = models.CharField(max_length=20, choices=ROLE_CHOICES, default='receptionist')
    active  = models.BooleanField(default=True)

    # Trainer-specific: which members are assigned to this trainer
    # (populated via Enrollment.trainer FK, not stored here)

    class Meta:
        indexes = [
            models.Index(fields=['gym', 'role']),
            models.Index(fields=['user']),
        ]
        verbose_name        = 'Staff Profile'
        verbose_name_plural = 'Staff Profiles'

    def __str__(self):
        return f"{self.user.username} — {self.get_role_display()} @ {self.gym}"

    # ── Role helpers ──────────────────────────────────────────────────────
    @property
    def is_super_admin(self):
        return self.user.is_superuser

    @property
    def is_gym_owner(self):
        return self.role == 'gym_owner'

    @property
    def is_trainer(self):
        return self.role == 'trainer'

    @property
    def is_receptionist(self):
        return self.role == 'receptionist'
    
class GymGSTProfile(models.Model):
    """One-to-one GST/billing profile per gym tenant."""
    gym = models.OneToOneField('Gym', on_delete=models.CASCADE, related_name='gst_profile') 
    # Legal identity
    legal_business_name = models.CharField(max_length=255, help_text="As per GST registration")
    gstin = models.CharField(max_length=15, blank=True, help_text="15-char GSTIN, blank if unregistered")
    is_gst_registered = models.BooleanField(default=False)

    # Registered address (used as "Place of Supply" origin + invoice header)
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    state_code = models.CharField(max_length=2, help_text="GST state code, e.g. '22' for Chhattisgarh")
    pincode = models.CharField(max_length=6)

    # Invoicing config
    invoice_series_prefix = models.CharField(max_length=10, default='INV',
        help_text="e.g. 'INV' -> INV/2026-27/0001")
    default_sac_membership = models.CharField(max_length=8, default='999652',
        help_text="SAC for gym/fitness services")
    signature_image = models.URLField(blank=True, help_text="Cloudinary URL of signature/stamp")

    composition_scheme = models.BooleanField(default=False,
        help_text="If true, issue Bill of Supply instead of Tax Invoice — no GST shown")

    class Meta:
        verbose_name = "GST Profile"


# ──────────────────────────────────────────────────────────────────────────────
# Platform Subscription Payment (Arrow SoftTech's own revenue ledger —
# records what a gym actually paid YOU to use EnterGYM. No invoicing, just a log.)
# ──────────────────────────────────────────────────────────────────────────────
class PlatformSubscriptionPayment(models.Model):
    gym          = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name="platform_payments")
    plan         = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="platform_payments")
    amount       = models.DecimalField(max_digits=10, decimal_places=2)
    paid_on      = models.DateField(default=timezone.now)
    period_start = models.DateField(help_text="Billing period this payment covers (start)")
    period_end   = models.DateField(help_text="Billing period this payment covers (end)")
    notes        = models.CharField(max_length=255, blank=True, help_text="e.g. UPI ref no., 'cash', 'renewal'")
    recorded_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="platform_payments_recorded")
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_on']
        indexes = [models.Index(fields=['gym', 'paid_on'])]
        verbose_name = "Platform Subscription Payment"
        verbose_name_plural = "Platform Subscription Payments"

    def __str__(self):
        return f"{self.gym.gym_name} — ₹{self.amount} on {self.paid_on}"
    
class PlatformSettings(models.Model):
    upi_id = models.CharField(
        max_length=100, blank=True,
        help_text="Your (Arrow SoftTech's) UPI ID for receiving gym subscription payments, e.g. yourname@oksbi"
    )
    upi_display_name = models.CharField(
        max_length=120, blank=True,
        help_text="Name shown in the gym owner's UPI app during payment, e.g. 'Arrow SoftTech'"
    )

    class Meta:
        verbose_name = "Platform Settings"
        verbose_name_plural = "Platform Settings"

    def __str__(self):
        return "Platform Settings"

    def save(self, *args, **kwargs):
        # Enforce singleton — always overwrite row with pk=1, never allow a second row.
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # prevent accidental deletion of the only settings row

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
    
PERMISSION_DEFINITIONS = [
    # (field_name, human label, group)
    ("can_create_enrollment",          "Create Enrollment",          "Members"),
    ("can_edit_enrollment",            "Edit Enrollment",            "Members"),
    ("can_delete_enrollment",          "Delete Enrollment",          "Members"),
    ("can_delete_duplicate_enrollment","Delete Duplicate Enrollment","Members"),
    ("can_renew_membership",           "Renew Membership",           "Members"),
    ("can_change_membership_plan",     "Change Membership Plan",     "Members"),

    ("can_collect_payment",            "Collect Payment",            "Billing"),
    ("can_refund_payment",             "Refund Payment",             "Billing"),
    ("can_generate_invoice",           "Generate Invoice",           "Billing"),
    ("can_delete_invoice",             "Delete Invoice",             "Billing"),
    ("can_manage_membership_plans",    "Manage Membership Plans",    "Billing"),

    ("can_manage_trainers",            "Manage Trainers",            "Staff"),
    ("can_manage_staff",               "Manage Staff",               "Staff"),

    ("can_view_dashboard",             "View Dashboard",             "Reports"),
    ("can_view_reports",               "View Reports",               "Reports"),
    ("can_view_revenue",               "View Revenue",               "Reports"),
    ("can_export_reports",             "Export Reports",             "Reports"),

    ("can_manage_settings",            "Settings",                   "Gym"),
    ("can_manage_upi",                 "UPI",                        "Gym"),
    ("can_manage_gst",                 "GST",                        "Gym"),
    ("can_manage_subscription",        "Manage Subscription",        "Gym"),
    ("can_delete_contacts",            "Delete Contacts",            "Gym"),

    ("can_manage_face_recognition",    "Face Recognition",           "Attendance"),
    ("can_manage_attendance",          "Attendance",                 "Attendance"),

    ("can_send_expiry_notifications",  "Send Expiry Reminder",       "Notifications"),
    ("can_manage_notifications",       "Push Notifications",         "Notifications"),
    ("can_send_whatsapp",              "Send WhatsApp Messages",     "Notifications"),
    ("can_manage_store",               "Store (General)",            "Store"),
    ("can_manage_products",            "Products",                   "Store"),
    ("can_manage_orders",              "Orders",                     "Store"),
]

REMINDER_DAYS_BEFORE_CHOICES = [
    (1, '1 Day Before'),
    (3, '3 Days Before'),
    (5, '5 Days Before'),
    (7, '7 Days Before'),
]

REMINDER_TIME_CHOICES = [
    (_time(9, 0), 'Morning (09:00)'),
    (_time(14, 0), 'Afternoon (14:00)'),
    (_time(18, 0), 'Evening (18:00)'),
]

TIMEZONE_CHOICES = [
    ('Asia/Kolkata', 'Asia/Kolkata (IST)'),
    ('Asia/Dubai', 'Asia/Dubai (GST)'),
    ('Europe/London', 'Europe/London (GMT/BST)'),
    ('America/New_York', 'America/New_York (ET)'),
    ('Australia/Sydney', 'Australia/Sydney (AET)'),
]

# Permissions granted to Trainers by default (attendance-only).
_TRAINER_DEFAULT_TRUE = {
    "can_manage_attendance",
    "can_view_dashboard",
}

class StaffPermission(models.Model):
    """
    Fine-grained, per-staff-member permission flags.
    OneToOne with StaffProfile — every receptionist/trainer gets exactly one row.
    Gym Owners and Super Admins never consult this table (see has_permission()) —
    it exists purely so an owner has something to toggle for their staff.
    """
    staff_profile = models.OneToOneField(
        "Gym.StaffProfile",
        on_delete=models.CASCADE,
        related_name="permissions",
    )

    # Dynamically attach every boolean field defined above.
    for _field_name, _label, _group in PERMISSION_DEFINITIONS:
        locals()[_field_name] = models.BooleanField(default=False, verbose_name=_label)
    del _field_name, _label, _group

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "Staff Permission"
        verbose_name_plural = "Staff Permissions"

    def __str__(self):
        return f"Permissions for {self.staff_profile.user.username}"

    def apply_role_defaults(self, role):
        """Reset every flag to the sensible default for `role`, then save."""
        if role == "gym_owner":
            for field_name, _, _ in PERMISSION_DEFINITIONS:
                setattr(self, field_name, True)
        elif role == "trainer":
            for field_name, _, _ in PERMISSION_DEFINITIONS:
                setattr(self, field_name, field_name in _TRAINER_DEFAULT_TRUE)
        else:  # receptionist (and any future non-privileged role)
            for field_name, _, _ in PERMISSION_DEFINITIONS:
                setattr(self, field_name, False)


@receiver(post_save, sender=StaffProfile)
def create_or_sync_staff_permission(sender, instance, created, **kwargs):
    """
    On StaffProfile creation: create the StaffPermission row with role-based
    defaults. We deliberately do NOT re-apply defaults on every subsequent
    save (e.g. an owner toggling `active`) — that would silently wipe out
    permissions an owner has already customized.
    """
    if not created:
        return
    perm, was_created = StaffPermission.objects.get_or_create(staff_profile=instance)
    if was_created:
        perm.apply_role_defaults(instance.role)
        perm.save()

# ── Orphan User Cleanup — audit log ─────────────────────────────────────────
class OrphanUserDeletionLog(models.Model):
    """
    Permanent record of every user deleted via the Orphan User Cleanup tool.
    Stored separately from the User row so the audit trail survives the delete.
    """
    deleted_user_id = models.PositiveIntegerField()
    username        = models.CharField(max_length=150, help_text="Phone number / username at time of deletion")
    email           = models.EmailField(blank=True)
    date_joined     = models.DateTimeField(null=True, blank=True)
    last_login      = models.DateTimeField(null=True, blank=True)
    deleted_by      = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name="orphan_deletions_performed")
    deleted_at      = models.DateTimeField(auto_now_add=True)
    reason          = models.CharField(max_length=255, default="Orphan user cleanup — not linked to any gym")

    class Meta:
        ordering = ['-deleted_at']
        indexes = [models.Index(fields=['deleted_at'])]
        verbose_name = "Orphan User Deletion Log"
        verbose_name_plural = "Orphan User Deletion Logs"

    def __str__(self):
        return f"Deleted user #{self.deleted_user_id} ({self.username}) by {self.deleted_by} at {self.deleted_at}"

def clear_gym_equipment_brands_cache(sender, instance, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        cache.delete(f"gym_equipment_brands_{instance.pk}")


@receiver([post_save, post_delete], sender=EquipmentBrand)
def clear_all_brand_caches_on_catalog_change(sender, instance, **kwargs):
    gym_ids = instance.gyms.values_list('pk', flat=True) if instance.pk else []
    for gym_id in gym_ids:
        cache.delete(f"gym_equipment_brands_{gym_id}")


# Wire up the m2m signal now that Gym.equipment_brands exists
m2m_changed.connect(clear_gym_equipment_brands_cache, sender=Gym.equipment_brands.through)
m2m_changed.connect(clear_gym_services_cache, sender=Gym.services.through)

class GymWhatsAppSettings(models.Model):
    STATUS_CHOICES = [
        ('not_configured', 'Not Configured'),
        ('pending',        'Pending Verification'),
        ('connected',      'Connected'),
        ('disconnected',   'Disconnected'),
        ('error',          'Error'),
    ]
 
    gym = models.OneToOneField(
        Gym, on_delete=models.CASCADE, related_name='whatsapp_settings'
    )
    enabled = models.BooleanField(
        default=False,
        help_text="Master switch. When False, no WhatsApp message is ever sent "
                   "for this gym, regardless of individual notification triggers.",
    )
 
    # ── Meta Business identity (all gym-owned, never shared) ────────────
    business_name = models.CharField(max_length=120, blank=True)
    phone_number = models.CharField(
        max_length=20, blank=True,
        validators=[E164_PHONE_VALIDATOR],
        help_text="Gym's WhatsApp Business number in E.164 format, e.g. +919876543210",
    )
    phone_number_id = models.CharField(max_length=64, blank=True)
    business_account_id = models.CharField(max_length=64, blank=True)
 
    # ── Secrets — encrypted at rest via Gym.fields.EncryptedTextField ────
    permanent_access_token = EncryptedTextField(blank=True, default='')
    webhook_verify_token = EncryptedTextField(
        blank=True, default='',
        help_text="DEPRECATED — no longer used. Webhook verification now "
                   "uses the platform-wide settings.WHATSAPP_VERIFY_TOKEN. "
                   "Retained on this model only to avoid a destructive "
                   "migration on existing rows; never read or written by "
                   "current code.",
    )
    webhook_secret = EncryptedTextField(
        blank=True, default='',
        help_text="DEPRECATED — no longer used. Webhook signature "
                   "validation now uses the platform-wide "
                   "settings.WHATSAPP_APP_SECRET. Retained on this model "
                   "only to avoid a destructive migration on existing "
                   "rows; never read or written by current code.",
    )
 
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='not_configured')
    verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    reminder_days_before = models.PositiveSmallIntegerField(
        default=3, choices=REMINDER_DAYS_BEFORE_CHOICES,
        help_text="How many days before membership expiry the WhatsApp reminder "
                   "should be sent. Only ONE reminder is sent at this exact "
                   "offset — not on every day counting down to it.",
    )
    reminder_time = models.TimeField(
        default=_time(9, 0), choices=REMINDER_TIME_CHOICES,
        help_text="Time of day (in this gym's configured timezone) when the "
                   "WhatsApp expiry reminder should be sent. Requires the "
                   "expiry-reminder cron to run at least as often as the "
                   "configured time slots (recommended: 09:00, 14:00, 18:00).",
    )
    send_post_expiry_reminder = models.BooleanField(
        default=True,
        help_text="Whether to send exactly one WhatsApp reminder the day after "
                   "membership expires (days_left == -1 only — not repeated on "
                   "subsequent overdue days).",
    )
    timezone = models.CharField(
        max_length=50, default='Asia/Kolkata', choices=TIMEZONE_CHOICES,
        help_text="IANA timezone used to evaluate reminder_time for this gym.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
 
    class Meta:
        verbose_name = "Gym WhatsApp Settings"
        verbose_name_plural = "Gym WhatsApp Settings"
 
    def __str__(self):
        return f"WhatsApp[{self.gym.gym_name}] — {self.get_status_display()}"
 
    def clean(self):
        """
        Mirrors Gym.clean()'s upi_enabled validation pattern: turning the
        master switch on requires the connection to actually be usable.
        Deliberately does NOT validate credentials against Meta's API here
        (that's verify_connection() in the service layer, which needs a
        live network call) — this is just "did the owner fill the form in".
        """
        super().clean()
        if self.enabled:
            from django.core.exceptions import ValidationError
            errors = {}
            if not self.phone_number_id.strip():
                errors['phone_number_id'] = "Phone Number ID is required to enable WhatsApp."
            if not self.business_account_id.strip():
                errors['business_account_id'] = "Business Account ID is required to enable WhatsApp."
            if not self.permanent_access_token.strip():
                errors['permanent_access_token'] = "Access Token is required to enable WhatsApp."
            if errors:
                raise ValidationError(errors)
 
    # ── State-transition helpers ─────────────────────────────────────────
    # Centralizes every status write here. The service layer must never
    # set .status / .last_error / .verified_at directly — always through
    # one of these three methods.
 
    def mark_connected(self):
        """
        Called after ANY successful Meta API call — not just an explicit
        Verify click. This makes status self-healing: if a gym was
        previously 'error' from a transient Meta outage, the next
        successful send flips it back to 'connected' automatically.
        """
        from django.utils import timezone
        update_fields = []
        if self.status != 'connected':
            self.status = 'connected'
            update_fields.append('status')
        if self.last_error:
            self.last_error = ''
            update_fields.append('last_error')
        self.verified_at = timezone.now()
        update_fields.append('verified_at')
        if update_fields:
            update_fields.append('updated_at')
            self.save(update_fields=update_fields)
 
    def mark_error(self, error_message: str):
        """
        Called after a failed Meta API call. Does NOT touch `enabled` —
        status is health/UI state only, `enabled` remains the owner's
        explicit master switch. A gym in status='error' with enabled=True
        will still be attempted on the next send; success there calls
        mark_connected() again.
        """
        self.status = 'error'
        self.last_error = error_message[:2000]  # bounded — UI field, not a log archive
        self.save(update_fields=['status', 'last_error', 'updated_at'])
 
    def mark_disconnected(self):
        """
        Explicit owner action (the 'Disconnect' button), distinct from
        mark_error: a deliberate opt-out, not a health signal. Also flips
        `enabled` off.
        """
        self.status = 'disconnected'
        self.enabled = False
        self.last_error = ''
        self.save(update_fields=['status', 'enabled', 'last_error', 'updated_at'])
 
    @property
    def is_operational(self):
        """
        Only `enabled` gates sending. `status` is health/UI information,
        surfaced in the dashboard, but does not itself block a send
        attempt — a transient Meta 5xx that set status='error' must not
        permanently lock the gym out; the next send attempt is allowed to
        try again and self-heal via mark_connected(). Credential
        completeness is enforced separately by the service layer's
        _get_operational_settings, not by this property.
        """
        return self.enabled
    
    @staticmethod
    def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
        import hashlib
        import hmac
        from django.conf import settings

        if not signature_header or not signature_header.startswith('sha256='):
            return False
        provided_digest = signature_header.split('=', 1)[1]
        expected_digest = hmac.new(
            key=settings.WHATSAPP_APP_SECRET.encode('utf-8'),
            msg=raw_body,
            digestmod=hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(provided_digest, expected_digest)
 
 
@receiver([post_save, post_delete], sender=GymWhatsAppSettings)
def clear_gym_whatsapp_settings_cache(sender, instance, **kwargs):
    """Mirrors clear_gym_logo_cache — dashboard connection-status badge
    and any future gym-config cache must never show stale data."""
    cache.delete(f"gym_whatsapp_settings_{instance.gym_id}")
 
 
# ──────────────────────────────────────────────────────────────────────────────
# WhatsApp Cloud API — per-message audit/delivery log
#
# One row per outbound WhatsApp message attempt. Gym-scoped like every
# other business model (Enrollment, Trainer, etc.) — uses GymManager so
# `WhatsAppMessageLog.objects` behaves consistently with the rest of the
# codebase's gym-scoped querysets.
# ──────────────────────────────────────────────────────────────────────────────
class WhatsAppMessageLog(models.Model):
    MESSAGE_TYPE_CHOICES = [
        ('text',                'Text'),
        ('template',            'Template'),
        ('image',               'Image'),
        ('document',            'Document'),
        ('location',            'Location'),
        ('interactive_buttons', 'Interactive Buttons'),
    ]
    STATUS_CHOICES = [
        ('queued',    'Queued'),
        ('sent',      'Sent'),
        ('failed',    'Failed'),
        ('delivered', 'Delivered'),  # populated later via webhook, if/when implemented
        ('read',      'Read'),       # populated later via webhook, if/when implemented
    ]
 
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True, related_name='whatsapp_message_logs')
    objects = GymManager()
 
    # Nullable + SET_NULL: a member record can be deleted (soft or hard)
    # long after a message about them was sent — the log is a permanent
    # audit trail and must survive that, matching EnrollmentDeletionLog's
    # philosophy of "the log outlives the thing it's about."
    member = models.ForeignKey(
        'AuthFit.Enrollment', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='whatsapp_message_logs',
    )
    phone = models.CharField(
        max_length=20,
        validators=[E164_PHONE_VALIDATOR],
        help_text="Destination WhatsApp number at send time, E.164 format — stored "
                   "independently of `member` so the log stays readable even if "
                   "member is later nulled out.",
    )
 
    message_type = models.CharField(max_length=20, choices=MESSAGE_TYPE_CHOICES)
    template_name = models.CharField(max_length=100, blank=True)
 
    # Idempotency key. Blank for ad-hoc/test sends that have no natural
    # business key; populated for every business notification trigger
    # (expiry reminder, payment confirmation, etc). Uniqueness enforced
    # PER GYM and only when non-blank — see the partial UniqueConstraint
    # in Meta.constraints below.
    deduplication_key = models.CharField(
        max_length=255, blank=True, default='', db_index=True,
        help_text="Deterministic key identifying the business event this message "
                   "represents, e.g. 'expiry_reminder:{enrollment_id}:{due_date}'. "
                   "Used to guarantee at-most-one successful send per event.",
    )
 
    message_id = models.CharField(
        max_length=128, blank=True, db_index=True,
        help_text="Meta's `messages[0].id` from a successful send — used to correlate "
                   "with delivery/read webhook events later.",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued', db_index=True)
 
    # HTTP status code stored as its own field rather than buried inside `response`.
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
 
    # Trimmed dict — message_id, Meta's `error` object if present, and
    # nothing else. Enforced by the service layer's _trim_response()
    # helper, not by this field itself (JSONField can't self-validate shape).
    response = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
 
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
 
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['gym', 'created_at']),
            models.Index(fields=['gym', 'status']),
            models.Index(fields=['gym', 'message_type']),
            models.Index(fields=['gym', 'deduplication_key']),
        ]
        constraints = [
            # Partial unique constraint: only enforced when a dedup key is
            # actually set. Two blank-key rows (ad-hoc sends/tests) must
            # NOT be treated as duplicates of each other.
            models.UniqueConstraint(
                fields=['gym', 'deduplication_key'],
                condition=~models.Q(deduplication_key=''),
                name='unique_whatsapp_dedup_key_per_gym',
            )
        ]
        verbose_name = "WhatsApp Message Log"
        verbose_name_plural = "WhatsApp Message Logs"
 
    def __str__(self):
        return f"{self.gym.gym_code} → {self.phone} [{self.message_type}] {self.status}"

class PushNotificationLog(models.Model):
    """
    Audit log for FCM + Web Push sends — the push-channel equivalent of
    WhatsAppMessageLog. Written to from AuthFit/notifications.py's
    _send_member_fcm / _send_member_web_push, and from
    member_messages/services.py's _send_message_fcm / _send_message_web_push.

    Kept as a SEPARATE table from WhatsAppMessageLog rather than unifying
    them — the two channels have different natural fields (WhatsApp has
    template_name/message_id from Meta; push has title/body directly)
    and different callers/failure modes. A single member-detail helper
    (get_member_notification_log in member_service.py) merges both for
    display, so this doesn't cost anything at the UI layer.
    """
    CHANNEL_CHOICES = [
        ('fcm', 'FCM (Mobile App)'),
        ('web_push', 'Web Push (Browser/PWA)'),
    ]
    NOTIF_TYPE_CHOICES = [
        ('plan_expiry',        'Plan Expiry Reminder'),
        ('plan_changed',       'Plan Changed'),
        ('renewal_reminder',   'Renewal Reminder'),
        ('member_message',     'Staff Message'),
    ]

    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True, related_name='push_notification_logs')
    objects = GymManager()

    # Same SET_NULL philosophy as WhatsAppMessageLog.member — the log
    # outlives the enrollment it was about.
    member = models.ForeignKey(
        'AuthFit.Enrollment', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='push_notification_logs',
    )

    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    notif_type = models.CharField(max_length=30, choices=NOTIF_TYPE_CHOICES, blank=True)

    title = models.CharField(max_length=255)
    body = models.TextField()

    success = models.BooleanField(db_index=True)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['gym', 'created_at']),
            models.Index(fields=['gym', 'success']),
        ]
        verbose_name = "Push Notification Log"
        verbose_name_plural = "Push Notification Logs"

    def __str__(self):
        return f"{self.gym.gym_code} → member={self.member_id} [{self.channel}] {'OK' if self.success else 'FAIL'}"

class AuditLog(models.Model):
    """
    Append-only audit trail for financial and administrative actions.
    Written by AuthFit.audit.log_action() — never constructed directly
    elsewhere, so every call site logs the same shape of data.
    """
    ACTION_CHOICES = [
        ('invoice_created',       'Invoice Created'),
        ('invoice_voided',        'Invoice Voided'),
        ('payment_received',      'Payment Received'),
        ('refund_issued',         'Refund Issued'),
        ('expense_added',         'Expense Added'),
        ('expense_edited',        'Expense Edited'),
        ('expense_deleted',       'Expense Deleted'),
        ('membership_renewed',    'Membership Renewed'),
        ('plan_changed',          'Plan Changed'),
        ('enrollment_deleted',    'Enrollment Deleted'),
    ]

    gym    = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True, related_name='audit_logs')
    staff_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    action = models.CharField(max_length=30, choices=ACTION_CHOICES, db_index=True)

    # Generic reference to whatever object this action concerns (Invoice,
    # Refund, Expense, Enrollment, ...) — stored as a plain string ID +
    # label rather than a GenericForeignKey, since the log must survive
    # even if the referenced row is later hard-deleted.
    object_type = models.CharField(max_length=50, blank=True)
    object_id   = models.CharField(max_length=50, blank=True)
    object_label = models.CharField(max_length=255, blank=True,
        help_text="Human-readable snapshot, e.g. invoice number or member name — "
                   "survives even if the referenced row is later deleted.")

    old_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['gym', 'created_at']),
            models.Index(fields=['gym', 'action']),
            models.Index(fields=['object_type', 'object_id']),
        ]
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'

    def delete(self, *args, **kwargs):
        raise PermissionError("AuditLog rows are a permanent audit trail and cannot be deleted.")

    def __str__(self):
        return f"{self.gym.gym_code} — {self.get_action_display()} — {self.object_label} ({self.created_at:%d %b %Y %H:%M})"

# ──────────────────────────────────────────────────────────────────────────────
# AI Credit Wallet — Register Scan (and future AI features)
# ──────────────────────────────────────────────────────────────────────────────
class GymAICredit(models.Model):
    """
    Per-gym AI credit wallet. One row per gym, auto-created with 10 free
    credits the moment the gym is created (see create_gym_ai_credit_wallet
    below). All balance mutations MUST go through Gym.ai_credit_service —
    never edit `balance` / `total_used` directly, or the ledger in
    AICreditTransaction will drift out of sync.
    """
    gym = models.OneToOneField(Gym, on_delete=models.CASCADE, related_name='ai_credit')
    balance = models.PositiveIntegerField(default=10)
    total_used = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Gym AI Credit Wallet'
        verbose_name_plural = 'Gym AI Credit Wallets'

    def __str__(self):
        return f"{self.gym.gym_name} — {self.balance} credits"


class AICreditTransaction(models.Model):
    """
    Append-only ledger of every AI credit change — free grant, Register Scan
    deduction, or Super Admin manual adjustment. Never edited or deleted,
    mirroring the AuditLog / EnrollmentDeletionLog pattern already used
    elsewhere in this codebase.
    """
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True, related_name='ai_credit_transactions')
    credits = models.IntegerField(help_text="Positive for a credit added, negative for a deduction.")
    balance_after = models.PositiveIntegerField()
    reason = models.CharField(max_length=255)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['gym', 'created_at'])]
        verbose_name = 'AI Credit Transaction'
        verbose_name_plural = 'AI Credit Transactions'

    def delete(self, *args, **kwargs):
        raise PermissionError("AICreditTransaction rows are a permanent audit trail and cannot be deleted.")

    def __str__(self):
        sign = '+' if self.credits >= 0 else ''
        return f"{self.gym.gym_code}: {sign}{self.credits} ({self.reason})"


@receiver(post_save, sender=Gym)
def create_gym_ai_credit_wallet(sender, instance, created, **kwargs):
    """New gyms automatically receive 10 free AI credits, logged as a
    normal AICreditTransaction so the ledger is complete from day one."""
    if not created:
        return
    wallet, wallet_created = GymAICredit.objects.get_or_create(gym=instance)
    if wallet_created:
        AICreditTransaction.objects.create(
            gym=instance,
            credits=wallet.balance,
            balance_after=wallet.balance,
            reason="+10 Free Credits",
            created_by=None,
        )
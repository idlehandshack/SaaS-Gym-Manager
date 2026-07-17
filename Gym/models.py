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
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

# ──────────────────────────────────────────────────────────────────────────────
# Subscription Plans (SaaS tiers defined by the software owner)
# ──────────────────────────────────────────────────────────────────────────────
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
    website         = models.URLField(blank=True)
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
    THEME_CHOICES = [
        ('default', 'Default'),
        ('blue',    'Blue'),
        ('black',   'Black'),
        ('pink',    'Pink'),
        ('green',   'Green'),
        ('red',     'Red'),
    ]
    theme = models.CharField(
        max_length=20,
        choices=THEME_CHOICES,
        default='default',
        help_text="Predefined UI color palette for this gym's dashboard/site."
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
    
    # ── Helpers ───────────────────────────────────────────────────────────
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
    
    class Meta:
        ordering  = ['gym_name']
        indexes   = [models.Index(fields=['gym_code'])]
        verbose_name        = 'Gym'
        verbose_name_plural = 'Gyms'


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

    ("can_manage_store",               "Store (General)",            "Store"),
    ("can_manage_products",            "Products",                   "Store"),
    ("can_manage_orders",              "Orders",                     "Store"),
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
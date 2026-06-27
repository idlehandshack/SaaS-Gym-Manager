"""
gyms/models.py
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
    name            = models.CharField(max_length=60, unique=True)   # "Starter", "Pro", "Enterprise"
    price_monthly   = models.DecimalField(max_digits=10, decimal_places=2)
    member_limit    = models.PositiveIntegerField(default=100)
    trainer_limit   = models.PositiveIntegerField(default=5)
    feature_flags   = models.JSONField(default=dict, blank=True)     # {"face_recognition": true, …}

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

    # ── White-label settings ──────────────────────────────────────────────
    logo            = CloudinaryField('gym_logo', null=True, blank=True)
    contact_email   = models.EmailField(blank=True)
    contact_phone   = models.CharField(max_length=15, blank=True)
    whatsapp_number = models.CharField(max_length=15, blank=True)
    address         = models.TextField(blank=True)
    city            = models.CharField(max_length=60, blank=True)
    website         = models.URLField(blank=True)

    # ── Geo-fence (per gym) ───────────────────────────────────────────────
    latitude        = models.FloatField(default=0.0)
    longitude       = models.FloatField(default=0.0)
    radius_meters   = models.FloatField(default=100.0)

    # Add this inside the Gym model, after the geo-fence fields and before created_at

    # ── Module feature flags (per-gym toggles) ────────────────────────────
    enable_store            = models.BooleanField(default=True,
        help_text="Supplement store & order management.")
    enable_attendance       = models.BooleanField(default=True,
        help_text="Geo-attendance and attendance analytics.")
    enable_face_recognition = models.BooleanField(default=True,
        help_text="Face recognition enrollment and auto check-in.")
    enable_trainers         = models.BooleanField(default=True,
        help_text="Trainer management module.")

    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    # ── Helpers ───────────────────────────────────────────────────────────
    @property
    def is_subscription_active(self):
        if not self.active:
            return False
        if self.subscription_end and timezone.now().date() > self.subscription_end:
            return False
        return True

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
    cache.delete(f"gym_logo_{instance.pk}")

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
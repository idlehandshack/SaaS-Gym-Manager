from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from cloudinary.models import CloudinaryField
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from datetime import timedelta
from Gym.models import Gym
from Gym.mixins import GymManager

# Create your models here.

class Contact(models.Model):
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True
    )
    name = models.CharField(max_length=25)
    email = models.EmailField()
    phonenumber = models.CharField(max_length=10)
    description = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True, blank=True)

    objects = GymManager()
    def __str__(self):
        return self.name


class Trainer(models.Model):
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True
    )
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other'),
    ]
    name = models.CharField(max_length=30)
    gender = models.CharField(
        max_length=1, choices=GENDER_CHOICES, default='M')
    address = models.TextField()
    phone = models.CharField(max_length=10)
    salary = models.IntegerField()
    objects = GymManager()
    def __str__(self):
        return self.name


class MembershipPlan(models.Model):
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True
    )
    plan = models.CharField(max_length=100)
    price = models.IntegerField()
    duration_days = models.IntegerField(default=30)
    objects = GymManager()
    def __str__(self):
        return f"{self.plan} - ₹{self.price}"


class Enrollment(models.Model):
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True
    )
    objects = GymManager()
    # ==============================
    # CHOICES
    # ==============================
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
    ]

    PAYMENT = [
        ("Done", 'Done'),
        ("Pending", 'Pending'),
    ]

    METHOD = [
        ('C', 'CASH'),
        ('U', 'UPI'),
        ('B', 'UPI + CASH'),
    ]

    # ==============================
    # BASIC INFO
    # ==============================
    unique_id = models.CharField(max_length=10, editable=False, db_index=True)

    user = models.ForeignKey(
    User,
    on_delete=models.CASCADE
)

    fullname = models.CharField(max_length=25)
    email = models.EmailField()
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    phone = models.CharField(max_length=10, db_index=True)
    address = models.TextField()
    reference = models.CharField(max_length=30, null=True, blank=True)

    # ==============================
    # MEMBERSHIP
    # ==============================
    selectPlan = models.ForeignKey(MembershipPlan, on_delete=models.CASCADE)
    trainer = models.ForeignKey(
        Trainer, on_delete=models.SET_NULL, null=True, blank=True
    )
    Amount = models.DecimalField(max_digits=10, decimal_places=2)
    paidAmount = models.DecimalField(default=0,max_digits=10,decimal_places=2,null=False)
    paymentDate = models.DateField(blank=True, null=True)
    paymentMethod = models.CharField(
        max_length=1, choices=METHOD, blank=True, null=True
    )
    pendingAmount = models.DecimalField(default=0,max_digits=10,decimal_places=2)
    pendingpaymentMethod = models.CharField(
        max_length=1, choices=METHOD, blank=True, null=True
    )
    pendingpaymentDate =  models.DateField(blank=True, null=True)
    paymentStatus = models.CharField(
        max_length=10, choices=PAYMENT, default="Pending"
    )
    last_expiry_notif_sent = models.DateField(null=True, blank=True)

    # ==============================
    # DATES
    # ==============================
    doj = models.DateField(auto_now_add=True)
    DueDate = models.DateField(blank=True, null=True,db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, db_index=True)

    # ==============================
    # 🔥 FACE SYSTEM (CLEAN)
    # ==============================
    face_enrolled = models.BooleanField(default=False)

    # Profile image
    face_image = CloudinaryField('image', null=True, blank=True)

    # Multiple embeddings
    face_embeddings = models.JSONField(default=list, blank=True)
    class Meta:
        unique_together = ('gym', 'unique_id')  # unique within a gym, not globally
    # ==============================
    # UNIQUE ID GENERATOR
    # ==============================
    def generate_unique_id(self):
        import random
        while True:
            uid = str(random.randint(1000, 9999))
            if not Enrollment.objects.filter(gym=self.gym, unique_id=uid).exists():
                return uid

    # ==============================
    # SAVE METHOD (CLEAN)
    # ==============================
    def save(self, *args, **kwargs):

        # Generate unique ID
        if not self.unique_id:
            self.unique_id = self.generate_unique_id()

        # Set amount automatically
        if self.selectPlan:
            self.Amount = self.selectPlan.price

            if not self.DueDate and self.selectPlan.duration_days:
                today = timezone.now().date()
                self.DueDate = today + timedelta(days=self.selectPlan.duration_days)
            self.pendingAmount = self.selectPlan.price - self.paidAmount

        super().save(*args, **kwargs)

    # ==============================
    # EXPIRY LOGIC
    # ==============================
    @property
    def is_expired(self):
        if self.DueDate:
            return timezone.now().date() > self.DueDate
        return False

    @property
    def days_remaining(self):
        if self.DueDate:
            return (self.DueDate - timezone.now().date()).days
        return None

    def __str__(self):
        return f"{self.unique_id} - {self.fullname}"


class Attendence(models.Model):
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateField(default=timezone.localdate)
    timestamp = models.TimeField(auto_now_add=True)
    objects = GymManager()
    class Meta:
        unique_together = ('gym', 'user', 'date')

    def __str__(self):
        if hasattr(self.user, "enrollment"):
            return f"{self.user.enrollment.unique_id}"
        return f"{self.user.username} - {self.date}"

class GymNotification(models.Model):
    """
    Admin-managed notification ticker on the homepage.
    Each active entry scrolls across the notification bar.
    """
    ICON_CHOICES = [
        ("🎉", "🎉 Party / Offer"),
        ("💪", "💪 Training"),
        ("🏖️", "🏖️ Summer / Season"),
        ("⚡", "⚡ Alert / Closure"),
        ("🏷️", "🏷️ Deal / Discount"),
        ("📢", "📢 Announcement"),
        ("", "No icon"),
    ]
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True
    )
    objects = GymManager()
    icon = models.CharField(
        max_length=5, choices=ICON_CHOICES, blank=True, default="📢")
    message = models.CharField(
        max_length=200, help_text="Short notification text (max 200 chars)")
    is_active = models.BooleanField(
        default=True, help_text="Uncheck to hide from homepage")
    created_at = models.DateTimeField(auto_now_add=True)
    order = models.PositiveSmallIntegerField(
        default=0, help_text="Lower number = shows first")

    class Meta:
        ordering = ["order", "created_at"]
        verbose_name = "Gym Notification"
        verbose_name_plural = "Gym Notifications"

    def __str__(self):
        return f"{self.icon} {self.message[:60]}"
    
@receiver([post_save, post_delete], sender=Enrollment)
def clear_enrollment_cache(sender, instance, update_fields=None, **kwargs):
    if update_fields == frozenset({'last_expiry_notif_sent'}):
        return

    uid    = instance.user_id
    gym_pk = instance.gym_id

    # Gym-scoped keys (match context_processors.py + geo_views.py + views.py)
    cache.delete(f"enrollment_status_{uid}_{gym_pk}")
    cache.delete(f"enrolled_{uid}_{gym_pk}")
    cache.delete(f"enrollment_{uid}_{gym_pk}")

    # User-scoped keys (profile image is per-user, not per-gym)
    cache.delete(f"profile_image_{uid}")

    # Admin analytics caches
    cache.delete(f"admin_revenue_{gym_pk}")
    cache.delete(f"face_users_{gym_pk}")


@receiver([post_save, post_delete], sender=GymNotification)
def clear_notification_cache(sender, instance=None, **kwargs):
    cache.delete(f"notifications_{instance.gym_id}")

@receiver([post_save, post_delete], sender=MembershipPlan)
def clear_plan_cache(sender,instance =None, **kwargs):
    cache.delete(f"membership_plans_{instance.gym_id}")


class UserDevice(models.Model):
    """
    FCM push token for a member's device (separate from StaffDevice,
    which is for admin/owner order alerts). One user can have multiple devices.
    """
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True
    )
    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='devices')
    fcm_token   = models.TextField(unique=True)
    device_name = models.CharField(max_length=120, blank=True)
    last_seen   = models.DateTimeField(auto_now=True)
    active      = models.BooleanField(default=True)
    objects = GymManager()
    def __str__(self):
        return f"{self.user.username} — {self.device_name} ({'Active' if self.active else 'Inactive'})"

    class Meta:
        ordering = ['-last_seen']
        verbose_name = 'User Device'
        verbose_name_plural = 'User Devices'


class EnrollmentTransfer(models.Model):
    """
    Created when a member who already has an ACTIVE enrollment at one gym
    confirms enrollment at a different gym. Visible only to the previous gym,
    which later decides whether to mark the old enrollment inactive or delete it.
    """

    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('inactive', 'Inactive'),
        ('deleted',  'Deleted'),
    ]

    member        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='enrollment_transfers')
    mobile_number = models.CharField(max_length=10, db_index=True)

    previous_gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name='outgoing_transfers')
    new_gym      = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name='incoming_transfers')

    # Nulled out automatically if the old gym later deletes the source row —
    # the transfer record itself is kept as a historical log either way.
    previous_enrollment = models.ForeignKey(
        Enrollment, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transfer_records'
    )

    # Snapshot of old-gym data at the moment of transfer.
    # Kept as plain fields (not FKs) so the history survives deletion of the
    # source enrollment / plan.
    previous_member_id      = models.CharField(max_length=10)
    previous_plan_name      = models.CharField(max_length=50, blank=True)
    previous_joining_date   = models.DateField(null=True, blank=True)
    previous_due_date       = models.DateField(null=True, blank=True)
    previous_pending_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    last_payment_amount     = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    last_payment_date       = models.DateField(null=True, blank=True)

    new_gym_joining_date = models.DateField(auto_now_add=True)

    status          = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    action_taken_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    action_date     = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['previous_gym', 'status'])]
        constraints = [
            # DB-level guarantee: only one PENDING transfer per source
            # enrollment at a time. Prevents duplicate records even under
            # concurrent/double-click submissions.
            models.UniqueConstraint(
                fields=['previous_enrollment'],
                condition=models.Q(status='pending'),
                name='unique_pending_transfer_per_enrollment',
            )
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.mobile_number}: {self.previous_gym} → {self.new_gym} ({self.status})"
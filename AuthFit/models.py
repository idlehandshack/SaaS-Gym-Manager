#AuthFit/models.py
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
import secrets
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
    charge = models.IntegerField()
    objects = GymManager()
    def __str__(self):
        return f"{self.name} - ₹{self.charge}"

class MembershipPlan(models.Model):
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True
    )
    plan = models.CharField(max_length=100)
    price = models.IntegerField()
    duration_days = models.IntegerField(default=30)
    show_on_home = models.BooleanField(
        default=True,
        help_text="Show this plan in the pricing section of your gym's public homepage."
    )
    objects = GymManager()

    def __str__(self):
        return f"{self.plan} - ₹{self.price}"


class Enrollment(models.Model):
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True)
    objects = GymManager()

    GENDER_CHOICES = [('M', 'Male'), ('F', 'Female')]
    PAYMENT = [("Done", 'Done'), ("Pending", 'Pending')]
    METHOD = [('C', 'CASH'), ('U', 'UPI'), ('B', 'UPI + CASH')]

    SOURCE_CHOICES = [
        ("OWNER", "Owner"),
        ("MEMBER", "Member"),
    ]

    unique_id = models.CharField(max_length=10, editable=False, db_index=True)
    membership_start_date = models.DateField(
        default=timezone.localdate,
        help_text="Actual membership start date. Used to calculate DueDate — "
                "lets owners backdate enrollment for members who joined "
                "before EnterGYM. Distinct from `doj` (record-creation date).",
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True
    )
    profile_completed = models.BooleanField(default=False)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default="OWNER")

    fullname = models.CharField(max_length=25)
    email = models.EmailField(blank=True ,null=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True ,null=True)
    phone = models.CharField(max_length=10, db_index=True)
    address = models.TextField(blank=True)
    reference = models.CharField(max_length=30, null=True, blank=True)
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
    initial_invoice_generated = models.BooleanField(
        default=False,
        help_text="Set once the up-front payment collected during Quick Enrollment "
                "has been converted into a Payment + Invoice.",
    )
    doj = models.DateField(auto_now_add=True)
    DueDate = models.DateField(blank=True, null=True,db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    face_enrolled = models.BooleanField(default=False)
    face_image = CloudinaryField('image', null=True, blank=True)
    face_embeddings = models.JSONField(default=list, blank=True)
    class Meta:
        unique_together = ('gym', 'unique_id')
        indexes = [
            models.Index(fields=['gym', 'unique_id']),
            models.Index(fields=['gym', 'phone']),
            models.Index(fields=['gym', 'paymentStatus']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['gym', 'phone'],
                condition=models.Q(user__isnull=True),
                name='unique_pending_enrollment_phone_per_gym',
            )
        ]
    def generate_unique_id(self):
        import random
        while True:
            uid = str(random.randint(1000, 9999))
            if not Enrollment.objects.filter(gym=self.gym, unique_id=uid).exists():
                return uid
    def save(self, *args, **kwargs):
        if not self.unique_id:
            self.unique_id = self.generate_unique_id()
        if self.selectPlan:
            self.Amount = self.selectPlan.price

            if not self.DueDate and self.selectPlan.duration_days:
                start_date = self.membership_start_date or timezone.localdate()
                self.DueDate = start_date + timedelta(days=self.selectPlan.duration_days)
            self.pendingAmount = self.selectPlan.price - self.paidAmount

        super().save(*args, **kwargs)
    @property
    def is_expired(self):
        if self.DueDate:
            return timezone.localdate() > self.DueDate
        return False

    @property
    def days_remaining(self):
        if self.DueDate:
            return (self.DueDate - timezone.localdate()).days
        return None

    def __str__(self):
        return f"{self.unique_id} - {self.fullname}"

class MembershipPlanChangeLogQuerySet(models.QuerySet):
    def delete(self, *args, **kwargs):
        raise PermissionError(
            "MembershipPlanChangeLog rows are a permanent audit trail and "
            "cannot be bulk-deleted."
        )
 
 
class MembershipPlanChangeLogManager(models.Manager):
    def get_queryset(self):
        return MembershipPlanChangeLogQuerySet(self.model, using=self._db)
 
 
class MembershipPlanChangeLog(models.Model):
    gym = models.ForeignKey(
        Gym, on_delete=models.CASCADE, db_index=True,
        related_name='plan_change_logs',
    )
    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.CASCADE,
        related_name='plan_change_logs',
    )
 
    old_plan = models.ForeignKey(
        MembershipPlan, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    new_plan = models.ForeignKey(
        MembershipPlan, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
 
    old_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
 
    old_due_date = models.DateField(null=True, blank=True)
    new_due_date = models.DateField(null=True, blank=True)
 
    reason = models.CharField(max_length=255, blank=True)
    changed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
 
    created_at = models.DateTimeField(auto_now_add=True)
 
    objects = MembershipPlanChangeLogManager()
 
    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['gym', 'enrollment'])]
        verbose_name = 'Membership Plan Change Log'
        verbose_name_plural = 'Membership Plan Change Logs'
 
    def delete(self, *args, **kwargs):
        raise PermissionError(
            "MembershipPlanChangeLog rows are a permanent audit trail and "
            "cannot be deleted."
        )
 
    def __str__(self):
        old_name = self.old_plan.plan if self.old_plan else "—"
        new_name = self.new_plan.plan if self.new_plan else "—"
        return f"{self.enrollment.unique_id}: {old_name} → {new_name} ({self.created_at:%d %b %Y})"
    
class EnrollmentDeletionLog(models.Model):
    DELETE_TYPE_CHOICES = [
        ('duplicate', 'Duplicate Enrollment (permanent)'),
        ('soft', 'Delete Enrollment Only (soft delete)'),
    ]
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True, related_name='enrollment_deletion_logs')
    gym_owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    enrollment_id = models.IntegerField(db_index=True)
    member_name = models.CharField(max_length=100)
    member_phone = models.CharField(max_length=10)

    delete_type = models.CharField(max_length=10, choices=DELETE_TYPE_CHOICES)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['gym', 'enrollment_id'])]
        verbose_name = 'Enrollment Deletion Log'
        verbose_name_plural = 'Enrollment Deletion Logs'

    def delete(self, *args, **kwargs):
        raise PermissionError("EnrollmentDeletionLog rows are a permanent audit trail and cannot be deleted.")

    def __str__(self):
        return f"{self.member_name} ({self.member_phone}) — {self.delete_type} — {self.created_at:%d %b %Y}"

class Attendence(models.Model):
    gym = models.ForeignKey(
        Gym,
        on_delete=models.CASCADE,
        db_index=True
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    enrollment = models.ForeignKey(
        'AuthFit.Enrollment',
        on_delete=models.CASCADE,
        null=True,         
        blank=True,
        related_name='attendance_logs',
        db_index=True,
    )
    date = models.DateField(default=timezone.localdate)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    objects = GymManager()

    class Meta:
        unique_together = ('gym', 'enrollment', 'date')

    def __str__(self):
        if self.enrollment_id:
            return f"{self.enrollment.unique_id}"
        if self.user:
            return f"{self.user.username} - {self.date}"
        return f"Attendance {self.pk} - {self.date}"
    
@receiver([post_save, post_delete], sender=Enrollment)
def clear_enrollment_cache(sender, instance, update_fields=None, **kwargs):
    if update_fields == frozenset({'last_expiry_notif_sent'}):
        return

    uid    = instance.user_id
    gym_pk = instance.gym_id

    cache.delete(f"enrollment_status_{uid}_{gym_pk}")
    cache.delete(f"enrolled_{uid}_{gym_pk}")
    cache.delete(f"enrollment_{uid}_{gym_pk}")
    cache.delete(f"profile_image_{uid}")
    cache.delete(f"admin_revenue_{gym_pk}")
    cache.delete(f"face_users_{gym_pk}")

@receiver([post_save, post_delete], sender=MembershipPlan)
def clear_plan_cache(sender,instance =None, **kwargs):
    cache.delete(f"membership_plans_{instance.gym_id}")


class UserDevice(models.Model):
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
    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('inactive', 'Inactive'),
        ('deleted',  'Deleted'),
    ]

    member        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='enrollment_transfers')
    mobile_number = models.CharField(max_length=10, db_index=True)

    previous_gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name='outgoing_transfers')
    new_gym      = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name='incoming_transfers')
    previous_enrollment = models.ForeignKey(
        Enrollment, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transfer_records'
    )
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
            models.UniqueConstraint(
                fields=['previous_enrollment'],
                condition=models.Q(status='pending'),
                name='unique_pending_transfer_per_enrollment',
            )
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.mobile_number}: {self.previous_gym} → {self.new_gym} ({self.status})"
    
class LoginSupportQuery(models.Model):
    PROBLEM_CHOICES = [
        ('forgot_password',  'Forgot Password'),
        ('unable_login',     'Unable to Login'),
        ('account_locked',   'Account Locked'),
        ('other',            'Other'),
    ]
    STATUS_CHOICES = [
        ('open',        'Open'),
        ('in_progress', 'In Progress'),
        ('resolved',    'Resolved'),
    ]
    gym  = models.ForeignKey(Gym, on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='login_support_queries')

    phone        = models.CharField(max_length=10, db_index=True)
    email        = models.EmailField()
    problem_type = models.CharField(max_length=20, choices=PROBLEM_CHOICES, db_index=True)
    description  = models.TextField()

    status      = models.CharField(max_length=15, choices=STATUS_CHOICES, default='open', db_index=True)
    created_at  = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    handled_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['phone']),
            models.Index(fields=['status']),
            models.Index(fields=['problem_type']),
            models.Index(fields=['created_at']),
        ]
        verbose_name = "Login Support Query"
        verbose_name_plural = "Login Support Queries"

    def __str__(self):
        return f"{self.phone} — {self.get_problem_type_display()} ({self.status})"

    def mark_resolved(self, staff_user):
        self.status = 'resolved'
        self.resolved_at = timezone.now()
        self.handled_by = staff_user
        self.save(update_fields=['status', 'resolved_at', 'handled_by'])

class GymQRCode(models.Model):
    """One permanent QR per gym. Regenerating invalidates the old one."""
    gym = models.OneToOneField(Gym, on_delete=models.CASCADE, related_name='qr_code')
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    regenerated_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = self._generate_token()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_token():
        return secrets.token_urlsafe(24)

    def regenerate(self):
        self.token = self._generate_token()
        self.regenerated_at = timezone.now()
        self.save(update_fields=['token', 'regenerated_at'])

    def __str__(self):
        return f"QR[{self.gym.gym_name}]"


class AttendanceAttempt(models.Model):
    REASON_CHOICES = [
        ('expired_plan', 'Expired Plan'),
        ('not_enrolled', 'Not Enrolled'),
        ('invalid_qr', 'Invalid QR'),
    ]
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True, null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    enrollment = models.ForeignKey(Enrollment, on_delete=models.SET_NULL, null=True, blank=True)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    attempted_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)

    class Meta:
        ordering = ['-attempted_at']
        indexes = [models.Index(fields=['gym', 'attempted_at'])]

    def __str__(self):
        return f"{self.user} - {self.reason} @ {self.gym}"

from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=Gym)
def create_gym_qr_code(sender, instance, created, **kwargs):
    if created:
        GymQRCode.objects.get_or_create(gym=instance)

class RegisterScanImport(models.Model):
    STATUS_CHOICES = [("pending", "Pending"), ("completed", "Completed")]
 
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True, related_name='register_scan_imports')
    imported_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
 
    image = CloudinaryField('image', null=True, blank=True)
    image_public_id = models.CharField(max_length=255, blank=True)
 
    raw_ai_response = models.JSONField(default=list, blank=True)   
    edited_response = models.JSONField(default=list, blank=True)   
 
    detected_count = models.PositiveIntegerField(default=0)        
    manual_count = models.PositiveIntegerField(default=0)          
    rows_edited = models.PositiveIntegerField(default=0)           
    saved_count = models.PositiveIntegerField(default=0)
    already_present_count = models.PositiveIntegerField(default=0)
    needs_review_count = models.PositiveIntegerField(default=0) 
    failed_count = models.PositiveIntegerField(default=0)
 
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    credit_consumed = models.BooleanField(
        default=False,
        help_text="True once one AI credit has been deducted for this import. "
                   "Guards against double-deduction if the same import is saved "
                   "more than once (refresh/resubmit).",
    )
    summary_broadcasted = models.BooleanField(
        default=False,
        help_text="True once the single Live Attendance summary notification "
                   "has been sent for this import. Guards against a duplicate "
                   "broadcast if save is retried/double-clicked for the same "
                   "import_batch.",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
 
    created_at = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['gym', 'created_at']), models.Index(fields=['gym', 'status'])]
        verbose_name = 'Register Scan Import'
        verbose_name_plural = 'Register Scan Imports'
 
    def __str__(self):
        return f"{self.gym.gym_name} — {self.saved_count}/{self.detected_count + self.manual_count} @ {self.created_at:%d %b %Y %H:%M}"
 
 
class RegisterScanImportRow(models.Model):
    SOURCE_CHOICES = [('ai', 'AI Detected'), ('manual', 'Manual')]
    STATUS_CHOICES = [('saved', 'Saved'), ('skipped_exists', 'Already Marked'), ('failed', 'Failed')]
 
    import_batch = models.ForeignKey(RegisterScanImport, on_delete=models.CASCADE, related_name='rows')
    unique_id = models.CharField(max_length=10, blank=True)
    detected_time = models.CharField(max_length=10, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    needs_review = models.BooleanField(default=False)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='ai')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    error_message = models.CharField(max_length=255, blank=True)
 
    class Meta:
        indexes = [models.Index(fields=['import_batch'])]
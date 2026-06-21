# AuthFit/admin.py

from django.contrib import admin
from django.urls import path
from django.utils.html import format_html
from django.db.models import Sum, Count, Max
from django.db.models.functions import ExtractWeekDay, ExtractHour, TruncMonth, TruncDay
from django.utils import timezone
from django.template.response import TemplateResponse
from django.core.cache import cache
from django.http import HttpResponseForbidden
from datetime import timedelta
from collections import defaultdict
import json

from cloudinary.utils import cloudinary_url

from .models import (
    Contact, Trainer, MembershipPlan, Attendence,
    GymNotification, Enrollment, UserDevice
)
from django.core.exceptions import PermissionDenied

# ──────────────────────────────────────────────────────────────────────────────
# Base admin mixin — scopes every changelist to request.gym
# ──────────────────────────────────────────────────────────────────────────────
class GymScopedAdmin(admin.ModelAdmin):
    """
    Base class for all AuthFit model admins.
    - Superuser sees everything (cross-gym).
    - Gym owner / staff sees and can only touch their own gym's data.
    """
 
    def get_gym(self, request):
        """Returns request.gym — None for superusers (sees all)."""
        return getattr(request, 'gym', None)
 
    # ── 1. Scope the changelist ───────────────────────────────────────────
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        gym = self.get_gym(request)
        if gym is None:
            return qs                        # superuser — all gyms
        return qs.filter(gym=gym)
 
    # ── 2. Hide gym field for non-superusers ──────────────────────────────
    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if not request.user.is_superuser and 'gym' in fields:
            fields.remove('gym')
        return fields
 
    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        # Superuser can see gym as readonly so they know which tenant owns the record
        if request.user.is_superuser and 'gym' not in readonly:
            readonly.append('gym')
        return readonly
 
    # ── 3. Auto-assign gym on save ────────────────────────────────────────
    def save_model(self, request, obj, form, change):
        gym = self.get_gym(request)
        if gym and not obj.gym_id:
            # New object created by gym staff — stamp with their gym
            obj.gym = gym
        elif gym and obj.gym_id and obj.gym_id != gym.pk:
            # Existing object belongs to a different gym — block it
            raise PermissionDenied(
                "You cannot modify records belonging to another gym."
            )
        super().save_model(request, obj, form, change)
 
    # ── 4. Prevent delete of other gym's objects ──────────────────────────
    def has_change_permission(self, request, obj=None):
        if obj is None:
            return True
        gym = self.get_gym(request)
        if gym and obj.gym_id != gym.pk:
            return False
        return True
 
    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return True
        gym = self.get_gym(request)
        if gym and obj.gym_id != gym.pk:
            return False
        return True
 
    # ── 5. Scope FK dropdowns to current gym ─────────────────────────────
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        gym = self.get_gym(request)
        if gym:
            if db_field.name == 'gym':
                # Never let non-superusers pick a gym — field is hidden anyway
                from Gym.models import Gym
                kwargs['queryset'] = Gym.objects.filter(pk=gym.pk)
            elif db_field.name == 'selectPlan' or db_field.name == 'plan':
                kwargs['queryset'] = MembershipPlan.objects.filter(gym=gym)
            elif db_field.name == 'trainer':
                kwargs['queryset'] = Trainer.objects.filter(gym=gym)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# Helper: get gym from request, enforce access
# ──────────────────────────────────────────────────────────────────────────────
def _get_gym_or_403(request):
    """
    Returns (gym_or_None, error_response_or_None).
    Super admins get gym=None (all-gym view).
    Regular staff get their gym or a 403.
    """
    if not (request.user.is_staff or request.user.is_superuser):
        return None, HttpResponseForbidden("Access denied.")
    if request.user.is_superuser:
        return None, None  # cross-gym view
    gym = getattr(request, 'gym', None)
    if gym is None:
        return None, HttpResponseForbidden("No gym associated with your account.")
    return gym, None

# ──────────────────────────────────────────────────────────────────────────────
# Model admins — all inherit GymScopedAdmin
# ──────────────────────────────────────────────────────────────────────────────
@admin.register(UserDevice)
class UserDeviceAdmin(GymScopedAdmin):
    list_display  = ['user', 'device_name', 'active', 'last_seen']
    list_filter   = ['active']
    search_fields = ['user__username', 'device_name']
    ordering      = ['-last_seen']
    readonly_fields = ['last_seen', 'fcm_token']
 
    def get_list_display(self, request):
        cols = list(self.list_display)
        if request.user.is_superuser and 'gym' not in cols:
            cols.insert(0, 'gym')
        return cols

@admin.register(GymNotification)
class GymNotificationAdmin(GymScopedAdmin):
    list_display  = ['icon', 'message', 'is_active', 'order', 'created_at']
    list_filter   = ['is_active']
    list_editable = ['is_active', 'order']
    search_fields = ['message']
    ordering      = ['order', 'created_at']
 
    # Superuser also sees which gym each notification belongs to
    def get_list_display(self, request):
        cols = list(self.list_display)
        if request.user.is_superuser and 'gym' not in cols:
            cols.insert(0, 'gym')
        return cols


@admin.register(Contact)
class ContactAdmin(GymScopedAdmin):
    list_display  = ['name', 'phonenumber', 'email', 'timestamp']
    search_fields = ['name', 'phonenumber', 'email']
    ordering      = ['-timestamp']
    readonly_fields = ['timestamp']
 
    def get_list_display(self, request):
        cols = list(self.list_display)
        if request.user.is_superuser and 'gym' not in cols:
            cols.insert(0, 'gym')
        return cols

@admin.register(Trainer)
class TrainerAdmin(GymScopedAdmin):
    list_display  = ['name', 'gender', 'phone', 'salary']
    search_fields = ['name', 'phone']
    ordering      = ['name']
 
    def get_list_display(self, request):
        cols = list(self.list_display)
        if request.user.is_superuser and 'gym' not in cols:
            cols.insert(0, 'gym')
        return cols


@admin.register(Attendence)
class AttendenceAdmin(GymScopedAdmin):
    list_display  = ['user', 'date', 'timestamp']
    list_filter   = ['date']
    search_fields = ['user__username']
    ordering      = ['-date', '-timestamp']
    readonly_fields = ['date', 'timestamp']
 
    def get_list_display(self, request):
        cols = list(self.list_display)
        if request.user.is_superuser and 'gym' not in cols:
            cols.insert(0, 'gym')
        return cols

    def _enrollment(self, obj):
        """Safe enrollment accessor — returns None if missing."""
        try:
            return obj.user.enrollment
        except Exception:
            return None

    def member_id(self, obj):
        e = self._enrollment(obj)
        return e.unique_id if e else '—'
    member_id.short_description = "Member ID"

    def member_name(self, obj):
        e = self._enrollment(obj)
        return e.fullname if e else obj.user.username
    member_name.short_description = "Name"

    def pending_amount(self, obj):
        e = self._enrollment(obj)
        return f"₹{e.pendingAmount}" if e else '—'
    pending_amount.short_description = "Pending ₹"

    def remaining_day(self, obj):
        e = self._enrollment(obj)
        return e.DueDate if e else '—'
    remaining_day.short_description = "Due Date"


@admin.register(Enrollment)
class EnrollmentAdmin(GymScopedAdmin):
    list_display  = [
        'unique_id', 'fullname', 'phone', 'selectPlan',
        'paymentStatus', 'DueDate', 'is_expired',
    ]
    list_filter   = ['paymentStatus', 'gender']
    search_fields = ['unique_id', 'fullname', 'phone']
    readonly_fields = ['unique_id', 'doj', 'created_at']
    ordering      = ['-created_at']
 
    @admin.display(boolean=True, description='Expired')
    def is_expired(self, obj):
        return obj.is_expired
 
    def get_list_display(self, request):
        cols = list(self.list_display)
        if request.user.is_superuser and 'gym' not in cols:
            cols.insert(0, 'gym')
        return cols

    def face_preview(self, obj):
        if not obj.face_image:
            return "No image"
        try:
            url, _ = cloudinary_url(
                obj.face_image.public_id,
                width=50, height=50,
                crop="fill", gravity="face",
                secure=True,
            )
            return format_html(
                '<img src="{}" width="50" height="50"'
                ' style="border-radius:50%;object-fit:cover;" />',
                url
            )
        except Exception:
            return "—"
    face_preview.short_description = "Photo"

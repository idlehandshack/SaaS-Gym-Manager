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
# Attendance analytics view — gym-scoped
# ──────────────────────────────────────────────────────────────────────────────
def attendance_view(request):
    gym, err = _get_gym_or_403(request)
    if err:
        return err

    # Cache key is per-gym (or 'superadmin' for cross-gym)
    cache_key = f"admin_attendance_data_{gym.pk if gym else 'super'}"
    cached = cache.get(cache_key)

    if cached is None:
        now = timezone.now()
        today = timezone.localdate()
        last_30 = now - timedelta(days=30)

        # Base queryset — scoped to gym
        qs = Attendence.objects.all()
        enroll_qs = Enrollment.objects.all()
        if gym:
            qs = qs.filter(gym=gym)
            enroll_qs = enroll_qs.filter(gym=gym)

        # Today vs yesterday
        today_count = qs.filter(date=today).count()
        yesterday_count = qs.filter(date=today - timedelta(days=1)).count()
        today_delta = today_count - yesterday_count

        # Day-of-week traffic
        day_map = {1: 'Sun', 2: 'Mon', 3: 'Tue',
                   4: 'Wed', 5: 'Thu', 6: 'Fri', 7: 'Sat'}
        ordered_dow = [2, 3, 4, 5, 6, 7, 1]
        day_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        dow = (
            qs.filter(date__gte=last_30.date())
            .annotate(dow=ExtractWeekDay('date'))
            .values('dow')
            .annotate(total=Count('id'))
            .order_by('dow')
        )
        dow_lookup = {d['dow']: d['total'] for d in dow}
        day_data = [dow_lookup.get(d, 0) for d in ordered_dow]

        # Hourly traffic
        hourly = (
            qs.filter(date__gte=last_30.date())
            .annotate(hr=ExtractHour('timestamp'))
            .values('hr')
            .annotate(total=Count('id'))
            .order_by('hr')
        )
        hour_lookup = {h['hr']: h['total'] for h in hourly}
        hour_range = list(range(5, 12)) + list(range(16, 23))

        def _fmt(h):
            hh = h if h <= 12 else h - 12
            suf = 'am' if h < 12 else 'pm'
            return f"{hh}{suf}" if h != 12 else '12p'

        hour_labels = [_fmt(h) for h in hour_range]
        hour_data = [hour_lookup.get(h, 0) for h in hour_range]

        peak_hr = max(hour_lookup, key=hour_lookup.get) if hour_lookup else 18
        next_hr = peak_hr + 1
        peak_hr_label = (
            f"{peak_hr if peak_hr<=12 else peak_hr-12}"
            f"{'am' if peak_hr<12 else 'pm'}"
            f" – "
            f"{next_hr if next_hr<=12 else next_hr-12}"
            f"{'am' if next_hr<12 else 'pm'}"
        )
        busiest_day = day_labels[day_data.index(
            max(day_data))] if day_data else '—'

        # Heatmap
        heatmap_raw = (
            qs.filter(date__gte=last_30.date())
            .annotate(dow=ExtractWeekDay('date'), hr=ExtractHour('timestamp'))
            .values('dow', 'hr')
            .annotate(total=Count('id'))
        )
        hm = defaultdict(lambda: defaultdict(int))
        for row in heatmap_raw:
            hm[row['dow']][row['hr']] = row['total']
        hm_hour_range = list(range(5, 12)) + list(range(16, 23))
        heatmap = {
            label: [hm[db_dow].get(h, 0) for h in hm_hour_range]
            for label, db_dow in zip(day_labels, ordered_dow)
        }

        # Monthly trend
        six_months_ago = now - timedelta(days=180)
        monthly = (
            qs.filter(date__gte=six_months_ago.date())
            .annotate(month=TruncMonth('date'))
            .values('month')
            .annotate(total=Count('id'))
            .order_by('month')
        )
        month_labels = [m['month'].strftime(
            "%b %Y") for m in monthly if m['month']]
        month_data = [m['total'] for m in monthly]

        # At-risk members — scoped to this gym's enrollments
        all_users_with_attendance = (
            qs.values('user_id').annotate(last_date=Max('date'))
        )
        at_risk = []
        for row in all_users_with_attendance:
            days_absent = (today - row['last_date']).days
            if days_absent >= 5:
                try:
                    enroll = enroll_qs.select_related('user').get(
                        user_id=row['user_id']
                    )
                    status = (
                        'danger' if days_absent >= 14 else
                        'warning' if days_absent >= 7 else
                        'notice'
                    )
                    at_risk.append({
                        'name':   enroll.fullname,
                        'uid':    enroll.unique_id,
                        'last':   row['last_date'].strftime("%b %d"),
                        'days':   days_absent,
                        'status': status,
                    })
                except Enrollment.DoesNotExist:
                    pass

        at_risk.sort(key=lambda x: -x['days'])
        at_risk = at_risk[:10]

        # Retention stats — scoped
        total_enrolled = enroll_qs.count()
        active_this_month = (
            qs.filter(date__year=today.year, date__month=today.month)
            .values('user').distinct().count()
        )
        retention_pct = (
            round(active_this_month / total_enrolled * 100, 1)
            if total_enrolled else 0
        )
        days_elapsed = today.day
        month_total = qs.filter(
            date__year=today.year, date__month=today.month
        ).count()
        avg_daily = round(month_total / days_elapsed, 1) if days_elapsed else 0

        cached = {
            "today_count":    today_count,
            "today_delta":    today_delta,
            "peak_hr_label":  peak_hr_label,
            "busiest_day":    busiest_day,
            "at_risk_count":  len([m for m in at_risk if m['status'] == 'danger']),
            "day_labels":     day_labels,
            "day_data":       day_data,
            "hour_labels":    hour_labels,
            "hour_data":      hour_data,
            "month_labels":   month_labels,
            "month_data":     month_data,
            "heatmap":        heatmap,
            "hour_range":     hour_range,
            "at_risk":        at_risk,
            "total_enrolled":     total_enrolled,
            "active_this_month":  active_this_month,
            "retention_pct":      retention_pct,
            "avg_daily":          avg_daily,
        }
        cache.set(cache_key, cached, timeout=120)

    context = dict(
        admin.site.each_context(request),
        **{k: json.dumps(v) if isinstance(v, (list, dict)) else v
           for k, v in cached.items()},
        # Override the ones the template needs as raw Python (not JSON)
        today_count=cached["today_count"],
        today_delta=cached["today_delta"],
        peak_hr_label=cached["peak_hr_label"],
        busiest_day=cached["busiest_day"],
        at_risk_count=cached["at_risk_count"],
        at_risk=cached["at_risk"],
        total_enrolled=cached["total_enrolled"],
        active_this_month=cached["active_this_month"],
        retention_pct=cached["retention_pct"],
        avg_daily=cached["avg_daily"],
        day_labels=json.dumps(cached["day_labels"]),
        day_data=json.dumps(cached["day_data"]),
        hour_labels=json.dumps(cached["hour_labels"]),
        hour_data=json.dumps(cached["hour_data"]),
        month_labels=json.dumps(cached["month_labels"]),
        month_data=json.dumps(cached["month_data"]),
        heatmap_json=json.dumps(cached["heatmap"]),
        hour_range_json=json.dumps(cached["hour_range"]),
    )
    return TemplateResponse(request, "admin/attendance_analysis.html", context)


# ──────────────────────────────────────────────────────────────────────────────
# Revenue analytics view — gym-scoped
# ──────────────────────────────────────────────────────────────────────────────
def revenue_view(request):
    gym, err = _get_gym_or_403(request)
    if err:
        return err

    # Per-gym cache key — critical fix
    cache_key = f"admin_revenue_{gym.pk if gym else 'super'}"
    data = cache.get(cache_key)

    if data is None:
        qs = Enrollment.objects.all()
        if gym:
            qs = qs.filter(gym=gym)

        monthly = (
            qs.annotate(month=TruncMonth('created_at'))
            .values('month')
            .annotate(total=Sum('Amount'))
            .order_by('month')
        )
        last_7_days = timezone.now() - timedelta(days=7)
        daily = (
            qs.filter(created_at__gte=last_7_days)
            .annotate(day=TruncDay('created_at'))
            .values('day')
            .annotate(total=Sum('Amount'))
            .order_by('day')
        )
        members = (
            qs.annotate(month=TruncMonth('created_at'))
            .values('month')
            .annotate(count=Count('id'))
            .order_by('month')
        )
        payments = (
            qs.exclude(paymentStatus__isnull=True)
            .values('paymentStatus')
            .annotate(count=Count('id'))
        )
        pending_qs = qs.filter(pendingAmount__gt=0, paymentStatus="Pending")
        pending_count = pending_qs.count()
        pending_amount = pending_qs.aggregate(
            total=Sum('pendingAmount'))['total'] or 0

        # Plan-wise revenue breakdown (new — useful per gym)
        plan_revenue = (
            qs.values('selectPlan__plan')
            .annotate(total=Sum('Amount'), count=Count('id'))
            .order_by('-total')
        )

        data = {
            "monthly_labels": [x['month'].strftime("%b %Y") for x in monthly if x['month']],
            "monthly_data":   [float(x['total'] or 0) for x in monthly],
            "daily_labels":   [x['day'].strftime("%d %b") for x in daily if x['day']],
            "daily_data":     [float(x['total'] or 0) for x in daily],
            "member_labels":  [x['month'].strftime("%b %Y") for x in members if x['month']],
            "member_data":    [x['count'] for x in members],
            "payment_labels": [x['paymentStatus'] for x in payments],
            "payment_data":   [x['count'] for x in payments],
            "plan_labels":    [x['selectPlan__plan'] or 'Unknown' for x in plan_revenue],
            "plan_revenue":   [float(x['total'] or 0) for x in plan_revenue],
            "plan_count":     [x['count'] for x in plan_revenue],
            "total_revenue":  sum(float(x['total'] or 0) for x in monthly),
            "today_revenue":  sum(float(x['total'] or 0) for x in daily),
            "total_members":  qs.count(),
            "pending_count":  pending_count,
            "pending_amount": float(pending_amount),
        }
        cache.set(cache_key, data, timeout=60)

    context = dict(
        admin.site.each_context(request),
        monthly_labels=json.dumps(data["monthly_labels"]),
        monthly_data=json.dumps(data["monthly_data"]),
        daily_labels=json.dumps(data["daily_labels"]),
        daily_data=json.dumps(data["daily_data"]),
        member_labels=json.dumps(data["member_labels"]),
        member_data=json.dumps(data["member_data"]),
        payment_labels=json.dumps(data["payment_labels"]),
        payment_data=json.dumps(data["payment_data"]),
        plan_labels=json.dumps(data["plan_labels"]),
        plan_revenue=json.dumps(data["plan_revenue"]),
        plan_count=json.dumps(data["plan_count"]),
        total_revenue=data["total_revenue"],
        today_revenue=data["today_revenue"],
        total_members=data["total_members"],
        pending_count=data["pending_count"],
        pending_amount=data["pending_amount"],
    )
    return TemplateResponse(request, "admin/revenue.html", context)


# ──────────────────────────────────────────────────────────────────────────────
# Custom URL injection
# ──────────────────────────────────────────────────────────────────────────────
original_get_urls = admin.site.get_urls


def custom_get_urls():
    urls = original_get_urls()
    custom_urls = [
        path('revenue/',    admin.site.admin_view(revenue_view)),
        path('attendance/', admin.site.admin_view(attendance_view)),
    ]
    return custom_urls + urls


admin.site.get_urls = custom_get_urls


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


@admin.register(MembershipPlan)
class MembershipPlanAdmin(GymScopedAdmin):
    list_display  = ['plan', 'price', 'duration_days']
    search_fields = ['plan']
    ordering      = ['price']
 
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

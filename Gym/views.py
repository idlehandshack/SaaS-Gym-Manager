from __future__ import annotations
from collections import OrderedDict
import re
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum, Q, DecimalField
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from datetime import timedelta
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.models import User
from .forms import UPISettingsForm, GymCreateForm, StaffProfileCreateForm, GymGSTProfileForm
from django.http import HttpResponseForbidden
from .models import Gym, SubscriptionPlan, StaffProfile ,PlatformSettings,GymGSTProfile ,SubscriptionPayment
from AuthFit.models import Enrollment ,GymQRCode, AttendanceAttempt
from .services import platform_insights as pi
import calendar
from urllib.parse import quote
from .services import live_stats as ls
from django.db.models import OuterRef, Subquery
from AuthFit.permissions import permission_required
from .models import OrphanUserDeletionLog
from .services import orphan_users as ou
import json as json_lib
from django.core.paginator import Paginator
import qrcode, io ,os
from django.http import HttpResponse
from AuthFit.notifications import notify_member_renewal_reminder
from django.conf import settings
from qrcode.constants import ERROR_CORRECT_H
from PIL import Image
from AuthFit.views import _gym_staff_required
from AuthFit.views import _get_gym
from Gym.services.platform_insights import invalidate_platform_insights_cache

QR_TEMPLATE_PATH = os.path.join(settings.BASE_DIR, "static", "images", "Attendance template.png")
 
# Region inside the template where the white QR panel sits (measured from the template)
QR_BOX = {
    "left": 820,
    "top": 1480,
    "right": 1865,
    "bottom": 2560,
}
QR_BOX_PADDING = 40 

def _qr_payload(qr_obj):
    return f"ENTERGYM-QR:{qr_obj.token}"

@_gym_staff_required
@require_POST
def gym_qr_regenerate(request):
    gym = getattr(request, 'gym', None)
    qr_obj, _ = GymQRCode.objects.get_or_create(gym=gym)
    qr_obj.regenerate()
    messages.success(request, "QR regenerated — the old QR is now invalid. Reprint and redisplay it at reception.")
    return redirect('gym_qr_settings')

def _build_qr_poster(payload: str, template_path: str = QR_TEMPLATE_PATH) -> bytes:
    """Render the QR code and paste it onto the branded template. Returns PNG bytes."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=20, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#080808", back_color="#ffffff").convert("RGB")

    template = Image.open(template_path).convert("RGB")

    box_w = QR_BOX["right"] - QR_BOX["left"]
    box_h = QR_BOX["bottom"] - QR_BOX["top"]
    target_size = min(box_w, box_h) - (QR_BOX_PADDING * 2)  # keep QR square

    qr_img = qr_img.resize((target_size, target_size), Image.NEAREST)

    # center the QR square inside the box (handles box not being perfectly square)
    paste_x = QR_BOX["left"] + (box_w - target_size) // 2
    paste_y = QR_BOX["top"] + (box_h - target_size) // 2
    template.paste(qr_img, (paste_x, paste_y))

    buf = io.BytesIO()
    template.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def gym_qr_download(request):
    gym = getattr(request, 'gym', None)
    qr_obj, _ = GymQRCode.objects.get_or_create(gym=gym)
    payload = _qr_payload(qr_obj)

    png_bytes = _build_qr_poster(payload)

    filename = f"{(gym.gym_name if gym else 'entergym')}-attendance-qr.png".replace(" ", "_")
    response = HttpResponse(png_bytes, content_type="image/png")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

@_gym_staff_required
@require_POST
def send_renewal_reminder(request, attempt_id):
    gym = getattr(request, 'gym', None)
    attempt = get_object_or_404(AttendanceAttempt, id=attempt_id, gym=gym, reason='expired_plan')
    enrollment = attempt.enrollment

    if not enrollment:
        return JsonResponse({'status': 'error', 'message': 'No enrollment on this attempt'}, status=400)

    sent = notify_member_renewal_reminder(enrollment)

    attempt.resolved = True
    attempt.save(update_fields=['resolved'])

    return JsonResponse({'status': 'sent' if sent else 'no_device', 'delivered': sent})

import json

@_gym_staff_required
def gym_qr_settings(request):
    gym = getattr(request, 'gym', None)
    qr_obj, _ = GymQRCode.objects.get_or_create(gym=gym)
    payload = _qr_payload(qr_obj)
    return render(request, "gym_qr_settings.html", {
        "gym": gym,
        "qr_payload": payload,
        "qr_payload_json": json.dumps(payload),
        "regenerated_at": qr_obj.regenerated_at,
    })

@permission_required("can_manage_upi")
def upi_payment_settings(request):
    gym = getattr(request, 'gym', None)
    if gym is None:
        raise PermissionDenied("No gym context available.")

    if request.method == "POST":
        form = UPISettingsForm(request.POST, instance=gym)
        if form.is_valid():
            form.save()
            from django.core.cache import cache
            cache.delete(f"gym_branding_{gym.pk}")
            messages.success(request, "UPI Payment Settings saved.")
            return redirect('upi_payment_settings')
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = UPISettingsForm(instance=gym)

    return render(request, "gym_settings_upi.html", {"gym": gym, "form": form})


def superuser_required(view_func):
    """Only Django superusers can pass. Everyone else gets 403."""
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper


def _filters_from_request(request) -> dict:
    """Extracts the shared top-bar filters from GET params."""
    return {
        "range": request.GET.get("range", "30d"),
        "date_from": request.GET.get("date_from") or None,
        "date_to": request.GET.get("date_to") or None,
        "plan": request.GET.get("plan") or None,
        "status": request.GET.get("status") or None,
        "state": request.GET.get("state") or None,
        "city": request.GET.get("city") or None,
        "search": request.GET.get("search") or None,
    }
def gst_profile_edit(request):
    gym = _get_gym(request)
    if gym is None:
        return HttpResponseForbidden("No gym context found for this request.")
 
    profile, created = GymGSTProfile.objects.get_or_create(
        gym=gym,
        defaults={
            "legal_business_name": gym.gym_name,
            "address_line1": "",
            "city": "",
            "state": "",
            "state_code": "",
            "pincode": "",
        },
    )
 
    if request.method == "POST":
        form = GymGSTProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Invoice / GST details updated successfully.")
            return redirect("gst_profile_edit")
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = GymGSTProfileForm(instance=profile)
 
    return render(
        request,
        "gst_profile.html",
        {
            "form": form,
            "gym": gym,
            "is_new": created,
        },
    )

@superuser_required
def platform_insights_page(request):
    """Renders the shell. Widgets load their data via the dashboard API."""
    plans = SubscriptionPlan.objects.all().order_by("name")
    return render(request, "insights.html", {"plans": plans})


# --------------------------------------------------------------------------- #
# Platform Insights — current API (3-request architecture)
# --------------------------------------------------------------------------- #
@superuser_required
def api_dashboard(request):
    """
    Aggregated dashboard payload — replaces ~11 separate widget calls.
    Cached for 5 minutes per filter combination. Each section is individually
    guarded inside get_dashboard(), so this can only fail on something outside
    that guard (e.g. a JSON-serialization edge case) — the except below exists
    purely so the endpoint still returns 200 with empty sections rather than 500.
    """
    try:
        return JsonResponse(pi.get_dashboard(_filters_from_request(request)))
    except Exception:
        return JsonResponse({
            "kpi_summary": {}, "platform_growth": {}, "member_growth": {},
            "gym_status": {}, "subscription_analytics": {}, "revenue_analytics": {},
            "member_distribution": {}, "platform_activity": {}, "engagement_analytics": {},
            "renewal_churn": {}, "payment_analytics": {}, "top_performing_gyms": {},
            "low_performing_gyms": {}, "geographic_analytics": {"available": False},
            "meta": {"generated_at": timezone.now().isoformat(), "cache_until": None, "version": 1},
        })


@superuser_required
def api_notifications(request):
    try:
        return JsonResponse(pi.get_notifications())
    except Exception:
        return JsonResponse({"unread_count": 0, "critical_count": 0, "notifications": []})


@superuser_required
def api_system_health(request):
    try:
        return JsonResponse(pi.get_system_health())
    except Exception:
        return JsonResponse({
            "database": {"status": "Down", "progress": 0},
            "redis": {"status": "Down", "progress": 0},
            "cron": {"status": "Failed", "last_run": None, "progress": 0},
            "cpu": {"usage": None},
            "memory": {"usage": None, "used": None, "total": None},
            "disk": {"usage": None, "used": None, "total": None},
            "web_push": {"status": "Unavailable", "progress": 0},
            "gunicorn": {"status": "Failed", "progress": 0},
            "nginx": {"status": "Failed", "progress": 0},
            "background_jobs": {"status": "Not Configured"},
            "uptime": None,
            "platform_status": "Critical",
        })


# --------------------------------------------------------------------------- #
# DEPRECATED — kept for backward compatibility only. All of these delegate
# to the exact same cached service functions used by api_dashboard, so there
# is a single source of business logic. Prefer /api/platform-insights/dashboard/.
# --------------------------------------------------------------------------- #
@superuser_required
def api_kpi_summary(request):
    return JsonResponse(pi.get_kpi_summary(_filters_from_request(request)))


@superuser_required
def api_platform_growth(request):
    return JsonResponse(pi.get_platform_growth(_filters_from_request(request)))


@superuser_required
def api_member_growth(request):
    return JsonResponse(pi.get_member_growth(_filters_from_request(request)))


@superuser_required
def api_subscription_analytics(request):
    return JsonResponse(pi.get_subscription_analytics(_filters_from_request(request)))


@superuser_required
def api_revenue_analytics(request):
    return JsonResponse(pi.get_revenue_analytics(_filters_from_request(request)))


@superuser_required
def api_member_distribution(request):
    return JsonResponse(pi.get_member_distribution(_filters_from_request(request)))


@superuser_required
def api_platform_activity(request):
    return JsonResponse(pi.get_platform_activity(_filters_from_request(request)))


@superuser_required
def api_engagement_analytics(request):
    return JsonResponse(pi.get_engagement_analytics(_filters_from_request(request)))


@superuser_required
def api_renewal_churn(request):
    return JsonResponse(pi.get_renewal_churn(_filters_from_request(request)))


@superuser_required
def api_payment_analytics(request):
    return JsonResponse(pi.get_payment_analytics(_filters_from_request(request)))


@superuser_required
def api_top_performing_gyms(request):
    return JsonResponse(pi.get_top_performing_gyms(_filters_from_request(request)))


@superuser_required
def api_low_performing_gyms(request):
    return JsonResponse(pi.get_low_performing_gyms(_filters_from_request(request)))

# --------------------------------------------------------------------------- #
# SaaS dashboard / gym management (unrelated to Platform Insights API)
# --------------------------------------------------------------------------- #
@superuser_required
def saas_dashboard(request):
    today = timezone.now().date()
    month_start = today.replace(day=1)
    revenue_subq = (
        Enrollment.objects  # make sure Enrollment is imported at top of views.py
        .filter(gym=OuterRef("pk"), is_deleted=False)
        .values("gym")
        .annotate(total=Sum("Amount"))
        .values("total")
    )
    gyms = (
        Gym.objects
        .select_related("plan", "owner")
        .annotate(
            member_count=Count("enrollment", distinct=True),
            trainer_count=Count(
                "staff",
                filter=Q(staff__role="trainer", staff__active=True),
                distinct=True,
            ),
            revenue=Coalesce(
                Subquery(revenue_subq, output_field=DecimalField(max_digits=12, decimal_places=2)),
                0,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )
        .order_by("-created_at")
    )
    
    total_gyms = gyms.count()
    active_gyms = gyms.filter(active=True, subscription_end__gte=today).count()
    inactive_gyms = total_gyms - active_gyms
    active_pct = round(active_gyms / total_gyms * 100) if total_gyms else 0
    inactive_pct = 100 - active_pct
    new_this_month = gyms.filter(created_at__date__gte=month_start).count()
    total_owners = gyms.values("owner").distinct().count()

    expiring_7 = gyms.filter(active=True, subscription_end__gte=today, subscription_end__lte=today + timedelta(days=7)).count()
    expiring_15 = gyms.filter(active=True, subscription_end__gt=today + timedelta(days=7), subscription_end__lte=today + timedelta(days=15)).count()
    expiring_30 = gyms.filter(active=True, subscription_end__gt=today + timedelta(days=15), subscription_end__lte=today + timedelta(days=30)).count()
    expired_count = gyms.filter(Q(active=False) | Q(subscription_end__lt=today)).count()

    capacity = gyms.aggregate(
        total_members=Coalesce(Sum("member_count"), 0),
        total_member_limit=Coalesce(Sum("member_limit"), 0),
    )
    total_members = capacity["total_members"]
    total_member_limit = capacity["total_member_limit"]
    near_member_limit = sum(
        1 for g in gyms
        if g.member_limit and g.member_count / g.member_limit >= 0.85
    )

    rev = gyms.aggregate(
        total=Coalesce(
            Sum("revenue"), 0,
            output_field=DecimalField(max_digits=14, decimal_places=2)
        )
    )
    total_revenue = rev["total"] or 0
    estimated_mrr = gyms.filter(active=True, subscription_end__gte=today).aggregate(
        mrr=Coalesce(
            Sum("plan__price_monthly"), 0,
            output_field=DecimalField(max_digits=10, decimal_places=2)
        )
    )["mrr"] or 0
    total_subscription_revenue = SubscriptionPayment.objects.aggregate(
        total=Coalesce(Sum("amount_paid"), 0, output_field=DecimalField(max_digits=14, decimal_places=2))
    )["total"] or 0

    plan_stats = [
        {"name": p.name, "count": gyms.filter(plan=p).count(), "monthly": p.price_monthly}
        for p in SubscriptionPlan.objects.all()
    ]

    return render(request, "saas_dashboard/saas_dashboard.html", {
        "gyms": gyms,
        "total_gyms": total_gyms,
        "active_gyms": active_gyms,
        "inactive_gyms": inactive_gyms,
        "expiring_7": expiring_7,
        "expiring_15": expiring_15,
        "expiring_30": expiring_30,
        "expired_count": expired_count,
        "new_this_month": new_this_month,
        "total_owners": total_owners,
        "active_pct": active_pct,
        "inactive_pct": inactive_pct,
        "near_member_limit": near_member_limit,
        "total_members": total_members,
        "total_member_limit": total_member_limit,
        "total_revenue": total_revenue,
        "total_subscription_revenue": total_subscription_revenue,
        "estimated_mrr": estimated_mrr,
        "plan_stats": plan_stats,
        "top_gyms": gyms.order_by("-revenue")[:5],
        "top_growing": gyms.filter(active=True).order_by("-member_count")[:6],
        "BASE_DOMAIN": "entergym.in",
    })

@superuser_required
@require_POST
def record_platform_payment(request, gym_id):
    from .models import PlatformSubscriptionPayment
    gym = get_object_or_404(Gym, pk=gym_id)

    amount = request.POST.get("amount")
    period_start = request.POST.get("period_start")
    period_end = request.POST.get("period_end")
    notes = request.POST.get("notes", "").strip()

    errors = {}
    try:
        amount = float(amount)
        if amount <= 0:
            errors["amount"] = ["Amount must be positive."]
    except (TypeError, ValueError):
        errors["amount"] = ["Enter a valid amount."]

    if not period_start or not period_end:
        errors["period"] = ["Both period start and end are required."]

    if errors:
        return JsonResponse({"success": False, "errors": errors}, status=400)

    payment = PlatformSubscriptionPayment.objects.create(
        gym=gym,
        plan=gym.plan,
        amount=amount,
        period_start=period_start,
        period_end=period_end,
        notes=notes,
        recorded_by=request.user,
    )
    return JsonResponse({"success": True, "payment_id": payment.id})


@superuser_required
def all_gyms_view(request):
    today = timezone.now().date()

    revenue_subq = (
        Enrollment.objects
        .filter(gym=OuterRef("pk"), is_deleted=False)
        .values("gym")
        .annotate(total=Sum("Amount"))
        .values("total")
    )

    gyms = (
        Gym.objects
        .select_related("plan", "owner")
        .annotate(
            member_count=Count("enrollment", distinct=True),
            trainer_count=Count(
                "staff", filter=Q(staff__role="trainer", staff__active=True), distinct=True,
            ),
            revenue=Coalesce(
                Subquery(revenue_subq, output_field=DecimalField(max_digits=12, decimal_places=2)),
                0,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )
        .order_by("-created_at")
    )
    return render(request, "all_gyms.html", {
        "gyms": gyms,
        "total_gyms": gyms.count(),
        "BASE_DOMAIN": "entergym.in",
        "today": today,
    })


@superuser_required
def gym_detail_json(request, gym_id):
    """Returns everything the detail modal needs: gym info, staff list, gst profile."""
    gym = get_object_or_404(Gym.objects.select_related("plan", "owner"), pk=gym_id)

    staff_qs = StaffProfile.objects.filter(gym=gym).select_related("user").order_by("role")
    staff_data = [
        {
            "id": s.id,
            "username": s.user.username,
            "email": s.user.email,
            "role": s.role,
            "role_display": s.get_role_display(),
            "active": s.active,
        }
        for s in staff_qs
    ]

    gst_profile = getattr(gym, "gst_profile", None)
    gst_data = None
    if gst_profile:
        gst_data = {
            "legal_business_name": gst_profile.legal_business_name,
            "gstin": gst_profile.gstin,
            "is_gst_registered": gst_profile.is_gst_registered,
            "address_line1": gst_profile.address_line1,
            "address_line2": gst_profile.address_line2,
            "city": gst_profile.city,
            "state": gst_profile.state,
            "state_code": gst_profile.state_code,
            "pincode": gst_profile.pincode,
            "invoice_series_prefix": gst_profile.invoice_series_prefix,
            "default_sac_membership": gst_profile.default_sac_membership,
            "composition_scheme": gst_profile.composition_scheme,
            "signature_image": gst_profile.signature_image,
        }

    return JsonResponse({
        "gym": {
            "id": str(gym.id),
            "gym_name": gym.gym_name,
            "gym_code": gym.gym_code,
            "owner_username": gym.owner.username,
            "owner_email": gym.owner.email,
            "plan": gym.plan.name if gym.plan else None,
            "active": gym.active,
            "contact_email": gym.contact_email,
            "contact_phone": gym.contact_phone,
        },
        "staff": staff_data,
        "gst_profile": gst_data,
    })


@superuser_required
@require_POST
def add_staff_profile(request, gym_id):
    gym = get_object_or_404(Gym, pk=gym_id)
    form = StaffProfileCreateForm(request.POST)
    if form.is_valid():
        staff = form.save(gym)
        return JsonResponse({
            "success": True,
            "staff": {
                "id": staff.id,
                "username": staff.user.username,
                "role_display": staff.get_role_display(),
                "active": staff.active,
            },
        })
    return JsonResponse({"success": False, "errors": form.errors}, status=400)


@superuser_required
@require_POST
def add_gst_profile(request, gym_id):
    gym = get_object_or_404(Gym, pk=gym_id)
    instance = getattr(gym, "gst_profile", None)  # create-or-update since it's OneToOne
    form = GymGSTProfileForm(request.POST, instance=instance)
    if form.is_valid():
        profile = form.save(commit=False)
        profile.gym = gym
        profile.save()
        return JsonResponse({"success": True})
    return JsonResponse({"success": False, "errors": form.errors}, status=400)


@superuser_required
def search_owner_by_phone(request):
    """AJAX: GET ?q=98 -> list of Users matching that phone/username prefix, excluding existing gym owners."""
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    users = (
        User.objects.filter(username__icontains=q)
        .select_related("owned_gym")
        .order_by("username")[:10]
    )
    results = [
        {
            "username": u.username,
            "full_name": u.get_full_name() or "—",
            "email": u.email or "—",
            "already_owns_gym": hasattr(u, "owned_gym"),
        }
        for u in users
    ]
    return JsonResponse({"results": results})


@superuser_required
def add_gym_page(request):
    """Full standalone page for creating a gym with every model field."""
    if request.method == "POST":
        form = GymCreateForm(request.POST, request.FILES)
        if form.is_valid():
            gym = form.save()
            messages.success(request, f"Gym '{gym.gym_name}' created successfully.")
            return redirect("all_gyms")
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = GymCreateForm()

    return render(request, "add_gym.html", {"form": form})


def _serialize_plan(plan, gyms_qs, today):
    gyms_on_plan = gyms_qs.filter(plan=plan)
    gyms_data = []
    for g in gyms_on_plan:
        gyms_data.append({
        "id": str(g.id),
        "gym_name": g.gym_name,
        "gym_code": g.gym_code,
        "owner_name": g.owner.get_full_name() or g.owner.username,
        "member_count": g.member_count,
        "member_limit": g.member_limit,
        "trainer_count": g.trainer_count,
        "trainer_limit": g.trainer_limit,
        "active": g.active,
        "is_subscription_active": g.is_subscription_active,
        "days_until_expiry": g.days_until_expiry,
        "subscription_end": g.subscription_end.isoformat() if g.subscription_end else None,
        "logo_url": g.logo.url if g.logo else None,
        "pending_amount": str(g.pending_amount),  
        "plan_id": str(g.plan_id) if g.plan_id else None,  
    })
    return {
        "id": plan.id,
        "name": plan.name,
        "price_monthly": str(plan.price_monthly),
        "member_limit": plan.member_limit,
        "trainer_limit": plan.trainer_limit,
        "feature_flags": plan.feature_flags or {},
        "gym_count": gyms_on_plan.count(),
        "gyms": gyms_data,
    }


@superuser_required
def subscriptions_page(request):
    today = timezone.now().date()

    gyms = (
        Gym.objects
        .select_related("plan", "owner")
        .annotate(
            member_count=Count("enrollment", distinct=True),
            trainer_count=Count(
                "staff",
                filter=Q(staff__role="trainer", staff__active=True),
                distinct=True,
            ),
        )
    )

    plans_qs = SubscriptionPlan.objects.all().order_by("price_monthly")
    plans_data = [_serialize_plan(p, gyms, today) for p in plans_qs]

    return render(request, "subscriptions_page.html", {
        "plans": plans_qs,
        "plans_json": json_lib.dumps(plans_data, default=str),
        "total_gyms": gyms.count(),
    })


@superuser_required
@require_POST
def add_subscription_plan(request):
    name = request.POST.get("name", "").strip()
    price_monthly = request.POST.get("price_monthly")
    member_limit = request.POST.get("member_limit")
    trainer_limit = request.POST.get("trainer_limit")
    feature_flags_raw = request.POST.get("feature_flags", "{}")

    errors = {}
    if not name:
        errors["name"] = ["Plan name is required."]
    elif SubscriptionPlan.objects.filter(name__iexact=name).exists():
        errors["name"] = ["A plan with this name already exists."]

    try:
        price_monthly = float(price_monthly)
        if price_monthly < 0:
            errors["price_monthly"] = ["Price cannot be negative."]
    except (TypeError, ValueError):
        errors["price_monthly"] = ["Enter a valid price."]

    try:
        member_limit = int(member_limit)
        if member_limit < 1:
            errors["member_limit"] = ["Must be at least 1."]
    except (TypeError, ValueError):
        errors["member_limit"] = ["Enter a valid number."]

    try:
        trainer_limit = int(trainer_limit)
        if trainer_limit < 1:
            errors["trainer_limit"] = ["Must be at least 1."]
    except (TypeError, ValueError):
        errors["trainer_limit"] = ["Enter a valid number."]

    try:
        feature_flags = json_lib.loads(feature_flags_raw)
    except json_lib.JSONDecodeError:
        feature_flags = {}

    if errors:
        return JsonResponse({"success": False, "errors": errors}, status=400)

    plan = SubscriptionPlan.objects.create(
        name=name,
        price_monthly=price_monthly,
        member_limit=member_limit,
        trainer_limit=trainer_limit,
        feature_flags=feature_flags,
    )
    return JsonResponse({"success": True, "plan_id": plan.id})


@superuser_required
@require_POST
def edit_subscription_plan(request, plan_id):
    plan = get_object_or_404(SubscriptionPlan, pk=plan_id)

    name = request.POST.get("name", "").strip()
    price_monthly = request.POST.get("price_monthly")
    member_limit = request.POST.get("member_limit")
    trainer_limit = request.POST.get("trainer_limit")
    feature_flags_raw = request.POST.get("feature_flags", "{}")

    errors = {}
    if not name:
        errors["name"] = ["Plan name is required."]
    elif SubscriptionPlan.objects.filter(name__iexact=name).exclude(pk=plan.pk).exists():
        errors["name"] = ["A plan with this name already exists."]

    try:
        price_monthly = float(price_monthly)
        if price_monthly < 0:
            errors["price_monthly"] = ["Price cannot be negative."]
    except (TypeError, ValueError):
        errors["price_monthly"] = ["Enter a valid price."]

    try:
        member_limit = int(member_limit)
        if member_limit < 1:
            errors["member_limit"] = ["Must be at least 1."]
    except (TypeError, ValueError):
        errors["member_limit"] = ["Enter a valid number."]

    try:
        trainer_limit = int(trainer_limit)
        if trainer_limit < 1:
            errors["trainer_limit"] = ["Must be at least 1."]
    except (TypeError, ValueError):
        errors["trainer_limit"] = ["Enter a valid number."]

    try:
        feature_flags = json_lib.loads(feature_flags_raw)
    except json_lib.JSONDecodeError:
        feature_flags = {}

    if errors:
        return JsonResponse({"success": False, "errors": errors}, status=400)

    plan.name = name
    plan.price_monthly = price_monthly
    plan.member_limit = member_limit
    plan.trainer_limit = trainer_limit
    plan.feature_flags = feature_flags
    plan.save()

    return JsonResponse({"success": True})


@superuser_required
@require_POST
def delete_subscription_plan(request, plan_id):
    plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
    if Gym.objects.filter(plan=plan).exists():
        return JsonResponse({
            "success": False,
            "error": "Cannot delete a plan that still has gyms assigned to it.",
        }, status=400)
    plan.delete()
    return JsonResponse({"success": True})


@superuser_required
@require_POST
def change_gym_plan(request, gym_id):
    gym = get_object_or_404(Gym, pk=gym_id)
    plan_id = request.POST.get("plan_id") or None

    if plan_id:
        plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
        gym.plan = plan
        gym.member_limit = plan.member_limit
        gym.trainer_limit = plan.trainer_limit
    else:
        gym.plan = None

    gym.save()
    return JsonResponse({"success": True})

@superuser_required
@require_POST
def enable_subscription_payment(request, gym_id):
    """
    Turns ON the 'Pay Subscription' button for exactly this gym's
    Owner/Receptionist. Does not touch any other gym's state.
    """
    gym = get_object_or_404(Gym, pk=gym_id)
    gym.show_subscription_payment = True
    gym.save(update_fields=["show_subscription_payment", "updated_at"])
    messages.success(request, f"Payment button enabled for '{gym.gym_name}'.")
    return JsonResponse({"success": True, "show_subscription_payment": True})


@superuser_required
@require_POST
def disable_subscription_payment(request, gym_id):
    """
    Turns OFF the 'Pay Subscription' button for this gym without changing
    anything else (no subscription dates touched).
    """
    gym = get_object_or_404(Gym, pk=gym_id)
    gym.show_subscription_payment = False
    gym.save(update_fields=["show_subscription_payment", "updated_at"])
    messages.success(request, f"Payment button disabled for '{gym.gym_name}'.")
    return JsonResponse({"success": True, "show_subscription_payment": False})


@superuser_required
@require_POST
def confirm_subscription_payment(request, gym_id):
    """
    Called by Super Admin AFTER manually confirming the bank credit.
    - Extends the subscription by 30 days from today
    - Re-activates the gym
    - Hides the payment button again (cycle resets)
    """
    gym = get_object_or_404(Gym, pk=gym_id)
    today = timezone.now().date()

    gym.subscription_start = today
    gym.subscription_end = today + timedelta(days=30)
    gym.show_subscription_payment = False
    gym.active = True
    gym.save(update_fields=[
        "subscription_start", "subscription_end",
        "show_subscription_payment", "active", "updated_at",
    ])

    messages.success(
        request,
        f"Payment confirmed for '{gym.gym_name}'. Subscription extended to "
        f"{gym.subscription_end.strftime('%d %b %Y')}."
    )
    return JsonResponse({
        "success": True,
        "subscription_end": gym.subscription_end.isoformat(),
        "show_subscription_payment": False,
        "active": True,
    })


@login_required
def gym_payment_page(request):
    gym = getattr(request, 'gym', None)
    if gym is None:
        raise PermissionDenied("No gym context available.")

    if request.staff_role not in ('gym_owner', 'receptionist'):
        raise PermissionDenied("You do not have permission to view this page.")

    if not gym.show_subscription_payment:
        raise PermissionDenied("Subscription payment is not currently enabled for your gym.")

    platform = PlatformSettings.load()

    upi_link = None
    if platform.upi_id:
        note = f"EnterGYM Subscription - {gym.gym_name}"
        upi_link = (
            "upi://pay?"
            f"pa={quote(platform.upi_id)}"
            f"&pn={quote(platform.upi_display_name or 'Arrow SoftTech')}"
            f"&tn={quote(note)}"
            "&cu=INR"
        )

    return render(request, "gym_payment_page.html", {
        "gym": gym,
        "upi_link": upi_link,
        "platform_upi_id": platform.upi_id,
        "platform_upi_name": platform.upi_display_name or "Arrow SoftTech",
    })

def api_public_live_stats(request):
    return JsonResponse(ls.get_live_stats())

def plans_page(request):
    """Public pricing page."""
    plans = SubscriptionPlan.objects.all().order_by("price_monthly")
    grouped_plans = OrderedDict()
    for plan in plans:
        flags = plan.feature_flags or {}
        # Feature chips
        plan.flag_items = [
            (key.replace("_", " ").title(),bool(value))
            for key, value in flags.items()
            if key != "featured"
        ]
        name = plan.name.strip()
        # Detect group
        if name.lower().startswith("free"):
            group = "Free Trial"
        else:
            group = re.split(r"\s*-\s*", name)[0]
        grouped_plans.setdefault(group, []).append(plan)
    return render(request,"plans_page.html",{
            "plan_groups": grouped_plans,
        },
    )

@superuser_required
def orphan_users_page(request):
    """
    Super Admin > User Cleanup.
    Lists visitor accounts (30+ days old by default) with zero gym/staff
    relationship, so they can be safely bulk-deleted.
    """
    qs = ou.orphan_users_base_queryset()
    qs = ou.apply_filters(qs, request)

    now = timezone.now()
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    rows = []
    for u in page_obj.object_list:
        age_days = (now - u.date_joined).days if u.date_joined else None
        rows.append({
            "id": u.id,
            "username": u.username,
            "full_name": u.get_full_name() or "—",
            "email": u.email or "—",
            "date_joined": u.date_joined,
            "last_login": u.last_login,
            "age_days": age_days,
            "status": "Never Logged In" if not u.last_login else "Inactive Visitor",
        })

    total_orphans = ou.orphan_users_base_queryset().count()

    return render(request, "orphan_users.html", {
        "rows": rows,
        "page_obj": page_obj,
        "total_orphans": total_orphans,
        "search": request.GET.get("search", ""),
        "age_filter": request.GET.get("age_filter", ""),
        "sort": request.GET.get("sort", "newest"),
    })


@superuser_required
@require_POST
def orphan_user_delete(request, user_id):
    """
    POST /superadmin/user-cleanup/<user_id>/delete/
    Re-validates the orphan condition immediately before deleting
    (protects against a race where the user joined a gym in the meantime),
    then logs the deletion for audit purposes.
    """
    is_orphan, reason = ou.revalidate_orphan(user_id)
    if not is_orphan:
        return JsonResponse({"success": False, "error": reason}, status=400)

    user = get_object_or_404(User, pk=user_id)

    OrphanUserDeletionLog.objects.create(
        deleted_user_id=user.id,
        username=user.username,
        email=user.email,
        date_joined=user.date_joined,
        last_login=user.last_login,
        deleted_by=request.user,
    )
    user.delete()

    return JsonResponse({"success": True})


@superuser_required
@require_POST
def orphan_user_bulk_delete(request):
    """
    POST /superadmin/user-cleanup/bulk-delete/
    Body: {"user_ids": [1, 2, 3]}
    Re-validates every user individually — a user that fails revalidation
    is skipped (not fatal to the rest of the batch) and reported back.
    """
    try:
        data = json_lib.loads(request.body)
    except json_lib.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    user_ids = data.get("user_ids", [])
    if not user_ids:
        return JsonResponse({"success": False, "error": "No users selected."}, status=400)

    deleted, skipped = [], []

    for uid in user_ids:
        is_orphan, reason = ou.revalidate_orphan(uid)
        if not is_orphan:
            skipped.append({"id": uid, "reason": reason})
            continue

        user = User.objects.filter(pk=uid).first()
        if not user:
            skipped.append({"id": uid, "reason": "User no longer exists."})
            continue

        OrphanUserDeletionLog.objects.create(
            deleted_user_id=user.id,
            username=user.username,
            email=user.email,
            date_joined=user.date_joined,
            last_login=user.last_login,
            deleted_by=request.user,
        )
        user.delete()
        deleted.append(uid)

    return JsonResponse({
        "success": True,
        "deleted_count": len(deleted),
        "skipped": skipped,
    })

def data_deletion(request):
    return render(request,'data_deletion.html')

@superuser_required
@require_POST
def renew_subscription(request, gym_id):
    gym = get_object_or_404(Gym, pk=gym_id)
    payment_status = request.POST.get("payment_status")
    plan_id = request.POST.get("plan_id")

    if payment_status in ("yes", "no"):
        amount_raw = request.POST.get("amount")
    else:
        return JsonResponse(
            {"success": False, "errors": {"payment_status": ["Invalid status."]}}, status=400
        )

    try:
        amount = float(amount_raw)
        if amount < 0:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse(
            {"success": False, "errors": {"amount": ["Enter a valid amount."]}}, status=400
        )

    if payment_status == "yes":
        plan = get_object_or_404(SubscriptionPlan, pk=plan_id) if plan_id else gym.plan
        if plan is None:
            return JsonResponse(
                {"success": False, "errors": {"plan_id": ["Select a plan."]}}, status=400
            )

        payment_date_raw = request.POST.get("payment_date")
        try:
            from datetime import timedelta, datetime as dt
            payment_date = dt.strptime(payment_date_raw, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return JsonResponse(
                {"success": False, "errors": {"payment_date": ["Enter a valid date."]}}, status=400
            )

        SubscriptionPayment.objects.create(
            gym=gym, plan=plan, amount_paid=amount,
            payment_date=payment_date, recorded_by=request.user,
        )

        gym.plan = plan
        gym.subscription_start = payment_date
        gym.subscription_end = payment_date + timedelta(days=30)
        gym.active = True
        gym.pending_amount = 0
        gym.show_subscription_payment = False
        gym.save(update_fields=[
            "plan", "subscription_start", "subscription_end",
            "active", "pending_amount", "show_subscription_payment", "updated_at",
        ])

        invalidate_platform_insights_cache()
        messages.success(
            request,
            f"Renewal recorded for '{gym.gym_name}'. Active until "
            f"{gym.subscription_end.strftime('%d %b %Y')}."
        )
        return JsonResponse({
            "success": True,
            "status": "paid",
            "subscription_end": gym.subscription_end.isoformat(),
            "pending_amount": "0.00",
        })

    else:  # payment_status == "no"
        gym.pending_amount = amount
        gym.save(update_fields=["pending_amount", "updated_at"])
        invalidate_platform_insights_cache()
        messages.warning(request, f"'{gym.gym_name}' marked pending ₹{amount:.2f}.")
        return JsonResponse({
            "success": True,
            "status": "pending",
            "pending_amount": f"{amount:.2f}",
        })
@superuser_required
@require_POST
def toggle_gym_status(request, gym_id):
    try:
        gym = Gym.objects.get(pk=gym_id)
    except Gym.DoesNotExist:
        return JsonResponse({"success": False, "error": "Gym not found."}, status=404)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid request."}, status=400)

    gym.active = bool(payload.get("active", not gym.active))
    gym.save(update_fields=["active", "updated_at"])
    return JsonResponse({"success": True, "active": gym.active})


@superuser_required
@require_POST
def gym_quick_edit(request, gym_id):
    try:
        gym = Gym.objects.get(pk=gym_id)
    except Gym.DoesNotExist:
        return JsonResponse({"success": False, "error": "Gym not found."}, status=404)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid request."}, status=400)

    member_limit = payload.get("member_limit")
    if member_limit is None or int(member_limit) < 0:
        return JsonResponse({"success": False, "error": "Member limit must be a positive number."}, status=400)

    gym.member_limit = int(member_limit)
    gym.app_download_url = (payload.get("app_download_url") or "").strip()
    gym.enable_store = bool(payload.get("enable_store"))
    gym.enable_attendance = bool(payload.get("enable_attendance"))
    gym.enable_face_recognition = bool(payload.get("enable_face_recognition"))
    gym.enable_trainers = bool(payload.get("enable_trainers"))

    gym.save(update_fields=[
        "member_limit", "app_download_url",
        "enable_store", "enable_attendance",
        "enable_face_recognition", "enable_trainers",
        "updated_at",
    ])
    return JsonResponse({"success": True})
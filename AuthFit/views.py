# AuthFit/views.py

import secrets
import os
import json
import tempfile
from datetime import date, datetime
from django.db.models import Q
import functools
from datetime import date, timedelta
from django.views.decorators.http import require_POST ,require_GET
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_log, logout
from django.http import JsonResponse,HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.paginator import Paginator
from django.db import transaction ,IntegrityError
import cloudinary.uploader
from cloudinary.utils import cloudinary_url
from PIL import Image
from django.db.models import Count
from django.core.exceptions import PermissionDenied
import io
import logging
from urllib.parse import urlencode
from Gym.models import Gym,GymWhatsAppSettings                         
from AuthFit.models import (
    Contact, Enrollment, EnrollmentTransfer, MembershipPlan, Trainer,
    Attendence as Attendence_model ,MembershipPlanChangeLog
)
from django.db.models import Prefetch
from django.http import HttpResponseRedirect
from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth,TruncDay
from AuthFit.rate_limit import check_login_attempt, reset_attempt, record_failed_attempt ,get_client_ip
from .forms import UserLogin , CompleteProfileForm , QuickEnrollmentForm ,GymExtrasForm
from Gym.ai_credit_service import get_or_create_wallet
from urllib.parse import quote
from Shop.notifications import notify_staff_new_enrollment
from django.contrib.auth.hashers import check_password
from billing.models import Invoice, Payment
from billing.services.gst_report import generate_gstr1_style_report
from billing.services.invoice_generator import create_invoice_for_payment
from billing.services.pdf_generator import generate_invoice_pdf
from billing.services.cloudflare_storage import upload_file_to_r2
from billing.services.change_membership_plan import (
        change_membership_plan, PlanChangeError)
from AuthFit.signals import _version_cache_key , _VERSION_CACHE_TTL ,update_enrollment_embeddings
from Gym.branding import get_gym_branding
from AuthFit.attendance import mark_attendance as mark_device_attendance ,mark_qr_attendance
from AuthFit.geo_logic import mark_geo_attendance
from reviews.models import Review
logger = logging.getLogger(__name__)
from AuthFit.permissions import permission_required
from Gym.utils.search import apply_search, get_search_context ,apply_related_search
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.template.loader import render_to_string
from django.core.mail import send_mail
from .forms import LoginSupportRequestForm
from AuthFit.models import LoginSupportQuery
from AuthFit.support_rate_limit import is_support_request_rate_limited
from Gym.utils.cloudinary_helpers import cloudinary_thumb
from AuthFit.decorators import active_member_required
from Gym.services.member_service import get_member_detail_queryset
from Gym.theme import THEME_PRESETS
from AuthFit.notifications import notify_member_plan_changed
from urllib.parse import quote
from Gym.dashboard_stat_cards import STAT_CARD_REGISTRY
ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
ALLOWED_EXTENSIONS  = {'.jpg', '.jpeg', '.png', '.webp'}
INTERNAL_API_KEY    = os.environ.get("INTERNAL_API_KEY", "")

MESSAGE_TEMPLATES = {
    "due": [
        {"key": "friendly", "label": "Friendly Reminder",
         "text": "Hello {name}! Reminder from {gym}: your payment of Rs.{amount} is pending. Please clear your dues at your earliest convenience. Thank you!"},
        {"key": "firm", "label": "Firm Follow-up",
         "text": "Hi {name}, this is a follow-up from {gym} regarding your outstanding payment of Rs.{amount}, due on {due_date}. Kindly clear it soon to keep your membership active."},
        {"key": "final", "label": "Final Notice",
         "text": "Dear {name}, your payment of Rs.{amount} to {gym} is still pending. This is a final reminder — please pay at the earliest to avoid suspension of services."},
    ],
    "expiring": [
        {"key": "friendly", "label": "Friendly Reminder",
         "text": "Hello {name}! Reminder from {gym}: your membership is expiring on {due_date}. Please renew soon to avoid interruption. Thank you!"},
        {"key": "offer", "label": "Renewal Nudge",
         "text": "Hi {name}, just a heads up — your membership at {gym} ends on {due_date}. Renew now to keep your streak going without any gap!"},
    ],
    "expired": [
        {"key": "friendly", "label": "Friendly Reminder",
         "text": "Hello {name}! Your membership at {gym} expired on {due_date}. Please renew to continue access. Thank you!"},
        {"key": "winback", "label": "We Miss You",
         "text": "Hi {name}, we miss you at {gym}! Your membership expired on {due_date}. Come back and renew today — we'd love to see you around again."},
    ],
}

def _gym_name(e):
    return e.gym.gym_name if e.gym else "EnterGYM"

def robots_txt(request):
    content = """
    User-agent: *
    Allow: /
    Sitemap: https://entergym.in/sitemap.xml
    """
    return HttpResponse(content, content_type="text/plain")

def _is_json_request(request):
    ct = request.META.get('CONTENT_TYPE', '')
    return 'application/json' in ct

def custom_403_view(request, exception=None):
    context = {
        'gym': getattr(request, 'gym', None),
    }
    return render(request, '403.html', context, status=403)

MANIFEST_CACHE_TTL = 60 * 60 * 6  # 6 hours
def manifest(request):
    gym = getattr(request, 'gym', None)
    cache_key = f"manifest_{gym.pk if gym else 'default'}"

    manifest_data = cache.get(cache_key)
    if manifest_data is None:
        b = get_gym_branding(gym)

        manifest_data = {
            "name": b["app_name"],
            "short_name": b["app_short_name"],
            "description": b["description"],
            "start_url": "/?source=pwa",
            "scope": "/",
            "display": "standalone",
            "background_color": b["background_color"],
            "theme_color": b["theme_color"],
            "orientation": "portrait-primary",
            "lang": "en",
            "categories": ["fitness", "health", "sports"],
            "icons": [
                {"src": b["logo_url"], "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": b["splash_logo_url"], "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": b["maskable_icon_url"], "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
                {"src": b["apple_touch_icon_url"], "sizes": "180x180", "type": "image/png", "purpose": "any"},
            ],
            "shortcuts": [
                {
                    "name": "Check In", "short_name": "Check In",
                    "description": "Mark your gym attendance",
                    "url": "/attendence/?source=shortcut",
                    "icons": [{"src": b["shortcut_icon_url"], "sizes": "96x96"}],
                },
                {
                    "name": "My Membership", "short_name": "Membership",
                    "description": "View membership & payment status",
                    "url": "/profile/?source=shortcut",
                    "icons": [{"src": b["shortcut_icon_url"], "sizes": "96x96"}],
                },
            ],
        }
        cache.set(cache_key, manifest_data, timeout=MANIFEST_CACHE_TTL)

    response = JsonResponse(manifest_data, json_dumps_params={"indent": 2})
    response["Content-Type"] = "application/manifest+json"
    response["Cache-Control"] = "public, max-age=3600"
    return response

def _superuser_required_local(view_fn):
    @login_required
    @functools.wraps(view_fn)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Super admin access required.")
        return view_fn(request, *args, **kwargs)
    return wrapped

def gym_favicon(request):
    gym = getattr(request, 'gym', None)

    if gym and gym.favicon:
        cache_key = f"gym_favicon_{gym.pk}"
        favicon_url = cache.get(cache_key)
        if favicon_url is None:
            try:
                public_id = (
                    gym.favicon.public_id
                    if hasattr(gym.favicon, 'public_id')
                    else str(gym.favicon)
                )
                favicon_url, _ = cloudinary_url(
                    public_id,
                    width=32, height=32,
                    crop="fill",
                    fetch_format="ico",
                    quality="auto",
                    secure=True,
                )
                cache.set(cache_key, favicon_url, timeout=86400)  # 24 hours
            except Exception:
                logger.exception("Cloudinary favicon URL error for gym %s", gym.pk)
                

        if favicon_url:
            return HttpResponseRedirect(favicon_url)
    from django.templatetags.static import static
    return HttpResponseRedirect(static('favicon.ico'))

def _send_password_reset_email(request, user, gym):
    token_generator = PasswordResetTokenGenerator()
    uid   = urlsafe_base64_encode(force_bytes(user.pk))
    token = token_generator.make_token(user)
    reset_url = request.build_absolute_uri(f"/accounts/reset/{uid}/{token}/")

    context = {
        "user": user,
        "reset_url": reset_url,
        "gym_name": gym.gym_name if gym else "EnterGYM",
        "gym": gym,
    }
    subject = "Reset Your EnterGYM Password"
    html_body = render_to_string("registration/password_reset_email.html", context)

    if user.email:
        try:
            send_mail(
                subject=subject,
                message=html_body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[user.email],
                html_message=html_body,
                fail_silently=False,
            )
        except Exception:
            logger.exception("Password reset email failed to send — user_id=%s", user.id)

def _build_attendance_entry(rec):
    enrollment = rec.enrollment  # direct FK on Attendence_model — same as today_attendance uses

    image_url = None
    if enrollment and enrollment.face_image:
        try:
            public_id = (
                enrollment.face_image.public_id
                if hasattr(enrollment.face_image, "public_id")
                else str(enrollment.face_image)
            )
            if public_id:
                image_url, _ = cloudinary_url(
                    public_id, width=60, height=60,
                    crop="fill", gravity="face",
                    fetch_format="auto", quality="auto", secure=True,
                )
        except Exception:
            logger.exception("Cloudinary URL error for user %s", getattr(rec.user, 'id', None))

    pending_amount = float(enrollment.pendingAmount) if enrollment else 0
    is_expired = enrollment.is_expired if enrollment else False
    days_remaining = enrollment.days_remaining if enrollment else None
    is_expiring_soon = (
        not is_expired and days_remaining is not None
        and days_remaining <= 3
    )
    has_pending = pending_amount > 0

    return {
        "id": rec.id,
        "time": timezone.localtime(rec.timestamp).strftime("%I:%M %p") if rec.timestamp else "—",
        "name": enrollment.fullname if enrollment else (rec.user.username if rec.user else "Unknown"),
        "unique_id": enrollment.unique_id if enrollment else "—",
        "image_url": image_url,
        "pending_amount": pending_amount,
        "due_date": enrollment.DueDate.strftime("%d %b %Y") if enrollment and enrollment.DueDate else "—",
        "is_expired": is_expired,
        "is_expiring_soon": is_expiring_soon,
        "has_pending": has_pending,
        "phone": enrollment.phone if enrollment else "—",
        "address": enrollment.address if enrollment else "—",
        "plan": enrollment.selectPlan.plan if enrollment and enrollment.selectPlan else "—",
        "plan_price": float(enrollment.selectPlan.price) if enrollment and enrollment.selectPlan else 0,
        "trainer": enrollment.trainer.name if enrollment and enrollment.trainer else "No Trainer",
        "gender": enrollment.get_gender_display() if enrollment else "—",
        "doj": enrollment.doj.strftime("%d %b %Y") if enrollment and enrollment.doj else "—",
        "payment_status": enrollment.paymentStatus if enrollment else "—",
        "days_remaining": days_remaining,
        "payment_date": enrollment.paymentDate.strftime("%d %b %Y") if enrollment and enrollment.paymentDate else "—",
    }

def _post_login_redirect(request, next_url):
    if request.user.is_superuser:
        return redirect('saas_dashboard')

    staff_role = getattr(request, 'staff_role', None)
    if staff_role in ('gym_owner', 'receptionist'):
        return redirect('dashboard_home')
    return redirect(_safe_next(next_url, request))

def _get_previous_days_attendance(gym, today, days=3):
    if gym is None:
        return []

    start_date = today - timedelta(days=days)
    end_date = today - timedelta(days=1)

    qs = (
        Attendence_model.objects
        .filter(gym=gym, date__range=(start_date, end_date))
        .select_related("user", "enrollment", "enrollment__selectPlan", "enrollment__trainer")
        .order_by("-date", "timestamp")
    )

    by_date = {}
    for rec in qs:
        by_date.setdefault(rec.date, []).append(_build_attendance_entry(rec))

    result = []
    for i in range(1, days + 1):
        d = today - timedelta(days=i)
        result.append({
            "date": d,
            "label": d.strftime("%A, %d %b"),
            "records": by_date.get(d, []),
            "count": len(by_date.get(d, [])),
        })
    return result

@require_POST
def login_support_submit(request):
    gym = getattr(request, 'gym', None)
    ip  = get_client_ip(request)

    form = LoginSupportRequestForm(request.POST)
    if not form.is_valid():
        non_field = form.errors.get('__all__')
        msg = non_field[0] if non_field else next(iter(form.errors.values()))[0]
        return JsonResponse({"error": msg}, status=400)

    phone        = form.cleaned_data['phone']
    email        = form.cleaned_data['email']
    problem_type = form.cleaned_data['problem_type']
    description  = form.cleaned_data['description']
    user         = form.cleaned_data['matched_user']

    if is_support_request_rate_limited(ip, phone):
        logger.warning("Support request rate-limited — phone=%s ip=%s", phone, ip)
        return JsonResponse({"error": "Too many requests. Please try again in an hour."}, status=429)

    if problem_type == 'forgot_password':
        _send_password_reset_email(request, user, gym)
        logger.info(
            "Password reset requested — phone=%s email=%s ip=%s ua=%s",
            phone, email, ip, request.META.get('HTTP_USER_AGENT', '')[:200],
        )
        return JsonResponse({
            "status": "success",
            "message": "A password reset link has been sent to your registered email.",
        })

    LoginSupportQuery.objects.create(
        gym=gym, user=user, phone=phone, email=email,
        problem_type=problem_type, description=description,
    )
    return JsonResponse({
        "status": "success",
        "message": "Your request has been submitted. Our team will get back to you soon.",
    })

@_superuser_required_local
def login_support_tickets(request):
    qs = LoginSupportQuery.objects.select_related('gym', 'user', 'handled_by').order_by('-created_at')

    status_filter = request.GET.get('status', '').strip()
    problem_filter = request.GET.get('problem_type', '').strip()
    phone_q = request.GET.get('phone', '').strip()
    email_q = request.GET.get('email', '').strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if problem_filter:
        qs = qs.filter(problem_type=problem_filter)
    if phone_q:
        qs = qs.filter(phone__icontains=phone_q)
    if email_q:
        qs = qs.filter(email__icontains=email_q)
    all_tickets = LoginSupportQuery.objects.all()

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, "admin/login_support_tickets.html", {
        "page_obj": page_obj,
        "status_choices": LoginSupportQuery.STATUS_CHOICES,
        "problem_choices": LoginSupportQuery.PROBLEM_CHOICES,
        "status_filter": status_filter,
        "problem_filter": problem_filter,
        "phone_q": phone_q,
        "email_q": email_q,
        "open_count": all_tickets.filter(status='open').count(),
        "in_progress_count": all_tickets.filter(status='in_progress').count(),
        "resolved_count": all_tickets.filter(status='resolved').count(),
    })


@_superuser_required_local
@require_POST
def login_support_ticket_resolve(request, ticket_id):
    ticket = get_object_or_404(LoginSupportQuery, pk=ticket_id)
    ticket.mark_resolved(request.user)
    return JsonResponse({"ok": True, "status": ticket.status})

def _check_internal_key(request):
    provided = request.headers.get("X-Internal-Key", "")
    if not INTERNAL_API_KEY or not provided:
        return False
    return secrets.compare_digest(provided, INTERNAL_API_KEY)

def _check_internal_key(request) -> bool:
    """Validate the shared secret sent by the attendance client."""
    from django.conf import settings
    expected = getattr(settings, "INTERNAL_API_KEY", "")
    return bool(expected) and request.headers.get("X-Internal-Key") == expected

def invalidate_gym_branding_cache(gym_pk):
    cache.delete(f"gym_favicon_{gym_pk}")
    cache.delete(f"gym_logo_{gym_pk}")


def get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _safe_next(next_url: str, request) -> str:
    if not next_url:
        return '/'
    if url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=not settings.DEBUG,
    ):
        return next_url
    return '/'

def _gym_from_request(request):
    return getattr(request, 'gym', None)

def _get_gym(request):
    if request.user.is_superuser:
        return None
    return getattr(request, 'gym', None)


def _gym_staff_required(view_fn):
    @login_required
    @functools.wraps(view_fn)
    def wrapped(request, *args, **kwargs):
        if not getattr(request, 'is_gym_staff', False):
            raise PermissionDenied("Staff access required.")
        return view_fn(request, *args, **kwargs)
    return wrapped


def _gym_role_required(*allowed_roles):
    def decorator(view_fn):
        @_gym_staff_required
        @functools.wraps(view_fn)
        def wrapped(request, *args, **kwargs):
            if not (request.is_super_admin or request.staff_role in allowed_roles):
                raise PermissionDenied("You don't have permission for this action.")
            return view_fn(request, *args, **kwargs)
        return wrapped
    return decorator

@_gym_staff_required
def gym_extras(request):
    if request.method == 'POST':
        form = GymExtrasForm(request.POST, gym=request.gym)
        if form.is_valid():
            form.save()
            cache.delete(f"gym_services_{request.gym.pk}")
            cache.delete(f"gym_equipment_brands_{request.gym.pk}")
            cache.delete(f"gym_social_links_{request.gym.pk}")
            cache.delete(f"membership_plans_{request.gym.pk}")
            messages.success(request, "Your selections were saved successfully.")
            return redirect('gym_extras')
    else:
        form = GymExtrasForm(gym=request.gym)

    selected_service_ids = set(request.gym.services.values_list('pk', flat=True))
    selected_brand_ids = set(request.gym.equipment_brands.values_list('pk', flat=True))
    service_catalog = [
        {'id': s.id, 'name': s.name, 'image_url': s.image.url if s.image else '',
         'selected': s.id in selected_service_ids}
        for s in form.fields['services'].queryset
    ]
    brand_catalog = [
        {'id': b.id, 'name': b.name, 'image_url': b.logo.url if b.logo else '',
         'selected': b.id in selected_brand_ids}
        for b in form.fields['brands'].queryset
    ]
    plan_catalog = [
        {'id': p.id, 'plan': p.plan, 'price': p.price, 'duration_days': p.duration_days,
         'selected': p.show_on_home}
        for p in form.fields['plans'].queryset
    ]

    hidden_cards = set(request.gym.hidden_stat_cards or [])
    stat_card_catalog = [
        {'key': key, 'label': label, 'selected': key not in hidden_cards}
        for key, label in STAT_CARD_REGISTRY
    ] 
    wa_settings = GymWhatsAppSettings.objects.filter(gym=request.gym).first()

    return render(request, 'gym_extras/index.html', {
        'form': form,
        'service_catalog': service_catalog,
        'brand_catalog': brand_catalog,
        'plan_catalog': plan_catalog,
        'stat_card_catalog': stat_card_catalog,
        'theme_presets': THEME_PRESETS,
        'wa_settings': wa_settings,
    })
@csrf_exempt
@require_POST
def gym_login_api(request):
    client_ip = get_client_ip(request)
 
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
 
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
 
    if not username or not password:
        return JsonResponse({"error": "username and password are required"}, status=400)
    if not check_login_attempt(client_ip, username):
        logger.warning("Face-client login rate-limited — username=%s ip=%s", username, client_ip)
        return JsonResponse(
            {"error": "Too many failed login attempts. Try again later."},
            status=429,
        )
 
    user = authenticate(request, username=username, password=password)
 
    if user is None:
        record_failed_attempt(client_ip, username)
        logger.warning("Face-client login failed for username=%s ip=%s", username, client_ip)
        return JsonResponse({"error": "Invalid username or password"}, status=401)
 
    if not user.is_active:
        return JsonResponse({"error": "This account is inactive"}, status=403)
 
    reset_attempt(client_ip, username)
    if user.is_superuser:
        return JsonResponse(
            {"error": "Super admin login via the face client is not supported here."},
            status=403,
        )
 
    staff_profile = getattr(user, "staff_profile", None)
    if staff_profile is None or staff_profile.role != "gym_owner":
        logger.warning(
            "Face-client login rejected — username=%s role=%s (owner-only endpoint)",
            username, getattr(staff_profile, "role", "none"),
        )
        return JsonResponse(
            {"error": "Only gym owners can log in to the face recognition client."},
            status=403,
        )
 
    if not staff_profile.active:
        return JsonResponse({"error": "This staff account has been deactivated"}, status=403)
 
    gym = staff_profile.gym or getattr(user, "owned_gym", None)
    if gym is None:
        logger.error("gym_owner user=%s has no associated gym", username)
        return JsonResponse({"error": "No gym is associated with this account"}, status=404)
 
    if not gym.is_subscription_active:
        return JsonResponse({"error": "This gym's subscription is not active"}, status=403)
 
    api_key = getattr(settings, "INTERNAL_API_KEY", "")
    if not api_key:
        logger.error("INTERNAL_API_KEY is not configured on the server")
        return JsonResponse({"error": "Server misconfiguration — contact support"}, status=500)
 
    base_url = getattr(settings, "PUBLIC_BASE_URL", None) or request.build_absolute_uri("/").rstrip("/")
 
    logger.info("Face-client login success — gym=%s (%s) username=%s", gym.gym_name, gym.id, username)
 
    return JsonResponse({
        "gym_id": str(gym.id),
        "gym_name": gym.gym_name,
        "base_url": base_url,
        "api_key": api_key,
        "role": "gym_owner",
    })

@csrf_exempt
def get_embedding_version(request):
    if not _check_internal_key(request):
        return JsonResponse({"error": "Unauthorized"}, status=403)
 
    gym_id = request.GET.get("gym_id")
    if not gym_id:
        return JsonResponse({"error": "gym_id required"}, status=400)
 
    cache_key = _version_cache_key(gym_id)
    version   = cache.get(cache_key)
 
    if version is None:
        try:
            from Gym.models import Gym  # Issue 2: correct import
 
            version = (
                Gym.objects
                .filter(pk=gym_id)
                .values_list("embedding_version", flat=True)
                .get()
            )
            cache.set(cache_key, version, timeout=_VERSION_CACHE_TTL)
            logger.debug(
                "embedding_version cache miss — fetched from DB: gym_id=%s version=%s",
                gym_id, version,
            )
 
        except Gym.DoesNotExist:
            return JsonResponse({"error": "Gym not found"}, status=404)
        except Exception:
            logger.exception(
                "Unexpected error in get_embedding_version for gym_id=%s", gym_id
            )
            return JsonResponse({"error": "Internal error"}, status=500)
 
    return JsonResponse({"version": version})

@csrf_exempt
def save_embeddings_batch(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        if not _check_internal_key(request):
            return JsonResponse({"error": "Unauthorized"}, status=403)

        data       = json.loads(request.body)
        unique_id  = data.get("unique_id")
        gym_id     = data.get("gym_id")
        embeddings = data.get("embeddings", [])

        if not unique_id:
            return JsonResponse({"error": "Missing unique_id"}, status=400)
        if not embeddings:
            return JsonResponse({"error": "Missing embeddings"}, status=400)

        qs = Enrollment.objects.filter(unique_id=unique_id)
        if gym_id:
            qs = qs.filter(gym_id=gym_id)
        enrollment = qs.get()

        updated = update_enrollment_embeddings(enrollment, embeddings)

        logger.info(
            "Embeddings saved — enrollment_id=%s  gym_id=%s  total=%d",
            enrollment.id, enrollment.gym_id, len(updated),
        )
        return JsonResponse({"status": "success", "total_embeddings": len(updated)})

    except Enrollment.DoesNotExist:
        return JsonResponse({"error": "Enrollment not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Unexpected error in save_embeddings_batch")
        return JsonResponse({"error": "Internal error"}, status=500)


@csrf_exempt
@require_POST
def mark_attendance_api(request):
    if not _is_json_request(request):
        return JsonResponse({'status': 'error', 'error': 'JSON required'}, status=415)

    try:
        body = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({'status': 'error', 'error': 'Invalid JSON'}, status=400)
    if 'unique_id' in body:
        unique_id = body.get('unique_id')
        gym_id = body.get('gym_id') or getattr(request, 'gym', None) and request.gym.pk
        result = mark_device_attendance(unique_id, gym_id=gym_id)
        status_code = 200 if result.get('status') in ('success', 'exists') else 400
        return JsonResponse(result, status=status_code)

    if 'lat' in body and 'lng' in body:
        if not request.user.is_authenticated:
            return JsonResponse({'status': 'error', 'error': 'Login required'}, status=401)

        try:
            lat = float(body['lat'])
            lng = float(body['lng'])
        except (TypeError, ValueError):
            return JsonResponse({'status': 'error', 'error': 'Invalid coordinates'}, status=400)

        gym = getattr(request, 'gym', None)
        result = mark_geo_attendance(request.user, gym, lat, lng)

        status_map = {
            'success': 200, 'exists': 200,
            'out_of_range': 403, 'not_enrolled': 403, 'expired': 403,
            'disabled': 403,
            'rate_limited': 429, 'error': 400,
        }
        return JsonResponse(result, status=status_map.get(result['status'], 400))
    
    
    if 'qr_code' in body:
        if not request.user.is_authenticated:
            return JsonResponse({'status': 'error', 'error': 'Login required'}, status=401)

        qr_token = body.get('qr_code')
        if not qr_token:
            return JsonResponse({'status': 'error', 'error': 'Missing qr_code'}, status=400)

        result = mark_qr_attendance(request.user, qr_token)

        status_map = {
            'success': 200, 'exists': 200,
            'expired_plan': 403, 'not_enrolled': 403, 'invalid_qr': 404,
            'error': 400,
        }
        return JsonResponse(result, status=status_map.get(result['status'], 400))

    return JsonResponse({'status': 'error', 'error': 'Missing unique_id, lat/lng, or qr_code'}, status=400)
    


@csrf_exempt
def get_users(request):
    if not _check_internal_key(request):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    gym_id = request.GET.get("gym_id")
    if not gym_id:
        return JsonResponse({"error": "gym_id required"}, status=400)

    cache_key = f"face_users_{gym_id}"
    data = cache.get(cache_key)
    if data is None:
        enrollments = Enrollment.objects.filter(gym_id=gym_id)

        data = [
            {
                "unique_id":  u.unique_id,
                "name":       u.fullname,
                "embeddings": u.face_embeddings,
            }
            for u in enrollments
        ]
        cache.set(cache_key, data, timeout=300)

    return JsonResponse(data, safe=False)


@csrf_exempt
def upload_face_image(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        if not _check_internal_key(request):
            return JsonResponse({"error": "Unauthorized"}, status=403)

        unique_id  = request.POST.get("unique_id")
        gym_id     = request.POST.get("gym_id")
        face_image = request.FILES.get("face_image")

        if not unique_id or not face_image:
            return JsonResponse({"error": "Missing unique_id or face_image"}, status=400)

        qs = Enrollment.objects.filter(unique_id=unique_id)
        if gym_id:
            qs = qs.filter(gym_id=gym_id)
        enrollment = qs.get()

        enrollment.face_image = face_image
        enrollment.save(update_fields=["face_image"])
        cache.delete(f"profile_image_{enrollment.user_id}")
        cache.delete(f"enrollment_{enrollment.user_id}_{enrollment.gym_id}")

        return JsonResponse({"status": "success", "image_url": enrollment.face_image.url})

    except Enrollment.DoesNotExist:
        return JsonResponse({"error": "Enrollment not found"}, status=404)
    except Exception:
        logger.exception("Error in upload_face_image")
        return JsonResponse({"error": "Internal error"}, status=500)


@csrf_exempt
def run_expiry_check(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        if not _check_internal_key(request):
            return JsonResponse({"error": "Unauthorized"}, status=403)

        from AuthFit.notifications import send_expiry_reminders
        count = send_expiry_reminders()
        return JsonResponse({"ok": True, "sent": count})
    except Exception:
        logger.exception("Error in run_expiry_check")
        return JsonResponse({"error": "Internal error"}, status=500)

@_gym_staff_required
def contact_inquiries(request):
    gym = getattr(request, 'gym', None)
    

    
    if gym is None:
        raise PermissionDenied("No gym context available.")

    if request.method == "POST":
        contact_id = request.POST.get("contact_id", "").strip()
        contact_obj = Contact.objects.filter(id=contact_id, gym=gym).first()
        if not contact_obj:
            messages.error(request, "Inquiry not found.")
            return redirect('/contact-inquiries/')

        contact_obj.delete()
        messages.success(request, "Inquiry deleted.")
        return redirect('/contact-inquiries/')

    query = request.GET.get("q", "").strip()
    qs = Contact.objects.filter(gym=gym).order_by('-timestamp')
    if query:
        qs = qs.filter(
            Q(name__icontains=query) |
            Q(phonenumber__icontains=query) |
            Q(email__icontains=query)
        )

    paginator = Paginator(qs, 20)
    page_obj  = paginator.get_page(request.GET.get("page", 1))

    return render(request, "contact_inquiries.html", {
        "gym":      gym,
        "page_obj": page_obj,
        "query":    query,
        "total":    qs.count(),
        
    })

def signupPage(request):
    if request.user.is_authenticated:
        return redirect('/')

    gym = getattr(request, 'gym', None)

    if request.method == "POST":
        form = UserLogin(request.POST, gym=gym)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()

                linked_enrollment = None
                if gym:
                    phone = getattr(user, 'username', None)
                    already_enrolled = Enrollment.objects.filter(user=user, gym=gym).exists()

                    if phone and not already_enrolled:
                        linked_enrollment = (
                            Enrollment.objects
                            .select_for_update()
                            .filter(gym=gym, phone=phone, user__isnull=True)
                            .first()
                        )
                        if linked_enrollment:
                            linked_enrollment.user = user
                            linked_enrollment.source = "MEMBER"    

                            update_fields = ['user', 'source']
                            if not linked_enrollment.email and user.email:  
                                linked_enrollment.email = user.email
                                update_fields.append('email')

                            linked_enrollment.save(update_fields=update_fields)

            auth_log(request, user)
            messages.success(request, "Account created successfully!")

            if linked_enrollment:
                cache.delete(f"enrollment_{user.id}_{gym.pk}")
                cache.delete(f"enrolled_{user.id}_{gym.pk}")
                cache.delete(f"enrollment_status_{user.id}_{gym.pk}")
                return redirect('/profile/')

            return redirect('/')
    else:
        form = UserLogin(gym=gym)

    signup_template = (
        "registration/saas_signup.html" if gym is None
        else "registration/signup.html"
    )
    return render(request, signup_template, {'form': form, 'gym': gym})


def loginPage(request):
    if request.user.is_authenticated:
        return redirect('/')

    next_url = request.GET.get('next') or request.POST.get('next', '/')
    gym = getattr(request, 'gym', None)
    if request.method == "POST":
        ip       = get_client_ip(request)
        phone    = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        if not check_login_attempt(ip, phone):
            messages.error(request, "Too many failed login attempts. Try again later.")
            return redirect(f'/login/?{urlencode({"next": next_url})}')

        user = authenticate(request, username=phone, password=password)
        if user is not None:
            reset_attempt(ip, phone)
            auth_log(request, user)
            messages.success(request, "Logged in successfully!")
            return _post_login_redirect(request,next_url)
        else:
            check_password(password, "pbkdf2_sha256$600000$dummy$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")
            record_failed_attempt(ip, phone)
            messages.error(request, "Incorrect phone number or password.")
            return redirect(f'/login/?{urlencode({"next": next_url})}')

    login_template = (
        "registration/saas_login.html" if gym is None
        else "registration/login.html"
    )

    return render(request, login_template, {'next': next_url, 'gym': gym ,})


def handlelogout(request):
    logout(request)
    messages.success(request, "Logged out successfully.")
    return redirect('/')

def homePage(request):
    gym = getattr(request, 'gym', None)
    if gym is None:
        verified_reviews = (
            Review.objects
            .filter(is_published=True, is_hidden=False)
            .select_related('gym', 'gym__owner')
            .order_by('-approved_at')[:9]
        )

        context = {'verified_reviews': verified_reviews}

        if request.user.is_superuser:
            context['gyms'] = Gym.objects.all().order_by('gym_name')

        return render(request, 'saas_home.html', context)
    plans_key = f"membership_plans_{gym.pk}"
    plans     = cache.get(plans_key)
    if plans is None:
        plans = list(
            MembershipPlan.objects
            .filter(gym=gym, show_on_home=True)
            .order_by('price')[:3]
            .values("id", "plan", "price", "duration_days")
        )
        cache.set(plans_key, plans, timeout=3600)
    
    services_key = f"gym_services_{gym.pk}"
    gym_services = cache.get(services_key)
    if gym_services is None:
        gym_services = [
            {
                'name': s.name,
                'description': s.description,
                'image_url': cloudinary_thumb(s.image, width=800, height=600, gravity="auto"),
            }
            for s in gym.services.filter(is_active=True).order_by('sort_order', 'name')
        ]
        cache.set(services_key, gym_services, timeout=3600)
 
    brands_key = f"gym_equipment_brands_{gym.pk}"
    gym_equipment_brands = cache.get(brands_key)
    if gym_equipment_brands is None:
        gym_equipment_brands = [
            {
                'name': b.name,
                'logo_url': cloudinary_thumb(b.logo, width=400, height=200, crop="fit", effect="trim"),
            }
            for b in gym.equipment_brands.filter(is_active=True).order_by('name')
        ]
        cache.set(brands_key, gym_equipment_brands, timeout=3600)
    social_key   = f"gym_social_links_{gym.pk}"
    social_links = cache.get(social_key)
    if social_links is None:
        social_links = gym.social_links
        cache.set(social_key, social_links, timeout=3600)

    enrolled    = False
    isStaff     = False
    isSuperuser = False

    if request.user.is_authenticated:
        isStaff     = getattr(request, 'is_gym_staff', False)
        isSuperuser = getattr(request, 'is_super_admin', False)

        cache_key = f"enrolled_{request.user.id}_{gym.pk}"
        enrolled  = cache.get(cache_key)
        if enrolled is None:
            enrolled = Enrollment.objects.filter(
                user=request.user, gym=gym
            ).exists()
            cache.set(cache_key, enrolled, timeout=300)

    geo_attendance_enabled = bool(gym.enable_geo_attendance)
    return render(request, 'gym_home.html', {
        "gym":               gym,
        "enrolled":          enrolled,
        "isStaff":           isStaff,
        "isSuperuser":       isSuperuser,
        "plans":             plans,
        "gym_services":         gym_services,          
        "gym_equipment_brands": gym_equipment_brands,    
        "geo_attendance_enabled": geo_attendance_enabled,
        "social_links":         social_links,
    })


def stats_api(request):
    gym = getattr(request, 'gym', None)
    qs  = Enrollment.objects.all()
    if gym:
        qs = qs.filter(gym=gym)
    return JsonResponse({"total_users": qs.count()})


def contact(request):
    gym = getattr(request, 'gym', None)
    if not gym:
        messages.error(request, "Contact form is unavailable on this domain.")
        return redirect('/')
    if gym.map:
        map_embed_url = gym.map
    elif gym.latitude and gym.longitude:
        map_embed_url = (
            f"https://maps.google.com/maps"
            f"?q={gym.latitude},{gym.longitude}"
            f"&z=16&output=embed"
        )
    else:
        map_embed_url = None
    if request.method == "POST":
        name    = request.POST.get('name', '').strip()
        number  = request.POST.get('number', '').strip()
        email   = request.POST.get('email', '').strip()
        message = request.POST.get('description', '').strip()

        if not number.isdigit() or len(number) != 10:
            messages.error(request, "Please enter a valid 10-digit phone number.")
            return redirect('/contact/')

        Contact.objects.create(
            gym=gym,
            name=name,
            email=email,
            phonenumber=number,
            description=message,
        )
        messages.success(request, "Thanks for contacting us — we'll get back to you soon!")
        return redirect('/contact/')
    


    return render(request, 'contact.html',{
        "gym":gym,
        "map_embed_url" : map_embed_url,
    })


def workout(request):

    gym = getattr(request, 'gym', None)
    return render(request, 'workout.html',{
        "gym":gym,
    })


def download_app(request):
    gym = getattr(request, 'gym', None) 
    gym_name  = gym.gym_name if gym else "EnterGYM"
    gym_short = gym_name.replace(" ", "") 
    return render(request, 'download.html', {
        'gym_name':  gym_name,
        'gym_short': gym_short,
    })


@permission_required("can_manage_membership_plans")
def membership_plans(request):
    gym = getattr(request, 'gym', None)
    if gym is None:
        raise PermissionDenied("No gym context available.")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "delete":
            plan_id = request.POST.get("plan_id")
            plan = MembershipPlan.objects.filter(id=plan_id, gym=gym).first()
            if not plan:
                messages.error(request, "Plan not found.")
                return redirect('/membership-plans/')
            if Enrollment.objects.filter(selectPlan=plan).exists():
                messages.error(
                    request,
                    f"Cannot delete '{plan.plan}' — it is in use by existing enrollments."
                )
                return redirect('/membership-plans/')
            plan.delete()
            cache.delete(f"membership_plans_{gym.pk}")
            messages.success(request, "Plan deleted.")
            return redirect('/membership-plans/')

        plan_id       = request.POST.get("plan_id", "").strip()
        plan_name     = request.POST.get("plan", "").strip()
        price_raw     = request.POST.get("price", "").strip()
        duration_raw  = request.POST.get("duration_days", "").strip()

        def fail(msg):
            messages.error(request, msg)
            return redirect('/membership-plans/')

        if not plan_name:
            return fail("Plan name is required.")

        try:
            price = int(price_raw)
            if price < 0:
                raise ValueError
        except (ValueError, TypeError):
            return fail("Enter a valid price.")

        try:
            duration_days = int(duration_raw) if duration_raw else 30
            if duration_days <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return fail("Enter a valid duration in days.")

        if plan_id:
            plan = MembershipPlan.objects.filter(id=plan_id, gym=gym).first()
            if not plan:
                return fail("Plan not found.")
            plan.plan          = plan_name
            plan.price         = price
            plan.duration_days = duration_days
            plan.save(update_fields=["plan", "price", "duration_days"])
            messages.success(request, f"'{plan_name}' updated.")
        else:
            MembershipPlan.objects.create(
                gym=gym,
                plan=plan_name,
                price=price,
                duration_days=duration_days,
            )
            messages.success(request, f"'{plan_name}' created.")

        cache.delete(f"membership_plans_{gym.pk}")
        return redirect('/membership-plans/')

    plans = MembershipPlan.objects.filter(gym=gym).order_by('price')
    return render(request, "membership_plans.html", {
        "gym":   gym,
        "plans": plans,
    })

@permission_required("can_manage_trainers")
def trainers(request):
    gym = getattr(request, 'gym', None)
    if gym is None:
        raise PermissionDenied("No gym context available.")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "delete":
            trainer_id = request.POST.get("trainer_id")
            trainer = Trainer.objects.filter(id=trainer_id, gym=gym).first()
            if not trainer:
                messages.error(request, "Trainer not found.")
                return redirect('/trainers/')
            trainer.delete()
            cache.delete(f"trainers_{gym.pk}")
            messages.success(request, "Trainer deleted successfully.")
            return redirect('/trainers/')

        trainer_id  = request.POST.get("trainer_id", "").strip()
        name        = request.POST.get("name", "").strip()
        gender      = request.POST.get("gender", "").strip()
        phone       = request.POST.get("phone", "").strip()
        address     = request.POST.get("address", "").strip()
        charge_raw = request.POST.get("charge", "").strip()

        def fail(msg):
            messages.error(request, msg)
            return redirect('/trainers/')
        if not name:
            return fail("Trainer name is required.")
        if len(name) > 30:
            return fail("Trainer name cannot exceed 30 characters.")

        if gender not in ("M", "F", "O"):
            return fail("Select a valid gender.")

        if not phone:
            return fail("Phone number is required.")
        if not phone.isdigit() or len(phone) != 10:
            return fail("Phone number must be exactly 10 digits.")

        if not address:
            return fail("Address is required.")

        try:
            charge = int(charge_raw)
            if charge < 0:
                raise ValueError
        except (ValueError, TypeError):
            return fail("Enter a valid non-negative salary.")

        if trainer_id:
            trainer = Trainer.objects.filter(id=trainer_id, gym=gym).first()
            if not trainer:
                return fail("Trainer not found.")
            trainer.name    = name
            trainer.gender  = gender
            trainer.phone   = phone
            trainer.address = address
            trainer.charge  = charge
            trainer.save(update_fields=["name", "gender", "phone", "address", "charge"])
            messages.success(request, "Trainer updated successfully.")
        else:
            Trainer.objects.create(
                gym=gym,
                name=name,
                gender=gender,
                phone=phone,
                address=address,
                charge=charge,
            )
            messages.success(request, "Trainer created successfully.")

        cache.delete(f"trainers_{gym.pk}")
        return redirect('/trainers/')
    trainers = Trainer.objects.filter(gym=gym).order_by("name")
    return render(request, "trainers.html", {
        "gym":      gym,
        "trainers": trainers,       
    })

@permission_required("can_create_enrollment")
def quick_enrollment(request):
    gym = getattr(request, 'gym', None)

    if request.method == "POST":
        form = QuickEnrollmentForm(request.POST, gym=gym)
        if form.is_valid():
            enrollment = form.save()
            transaction.on_commit(lambda: notify_staff_new_enrollment(enrollment))
            cache.delete(f"admin_revenue_{gym.pk}")

            if enrollment.user_id:
                messages.success(request, f"{enrollment.fullname} enrolled and linked to their existing account.")
            else:
                messages.success(request, f"{enrollment.fullname} enrolled — pending signup link.")

            return redirect(
                f"/quick-enrollment/?enrolled_id={enrollment.unique_id}&enrolled_name={quote(enrollment.fullname)}"
            )
    else:
        form = QuickEnrollmentForm(gym=gym)

    rows = (
        Enrollment.objects
        .filter(gym=gym)
        .select_related('selectPlan', 'trainer')
        .order_by('-doj')[:10]
    )
    pending = [
        {
            "id":             e.id,
            "name":           e.fullname,
            "phone":          e.phone,
            "plan":           e.selectPlan.plan if e.selectPlan else "—",
            "trainer":        e.trainer.name if e.trainer else "—",
            "payment_status": e.paymentStatus,
            "created":        e.doj.strftime("%d %b %Y") if e.doj else "—",
            "is_registered":  e.user_id is not None,
        }
        for e in rows
    ]
    return render(request, "quick_enrollment.html", {
        "form": form,
        "pending": pending,
        "gym": gym,
        "today_iso": timezone.localdate().isoformat(),
        "enrolled_id": request.GET.get("enrolled_id"),
        "enrolled_name": request.GET.get("enrolled_name"),
    })

@login_required
def enrollment(request):
    gym = getattr(request, 'gym', None)    
    if Enrollment.objects.filter(user=request.user, gym=gym,is_deleted=False).exists():
        return redirect('/profile/')

    plans    = MembershipPlan.objects.filter(gym=gym) if gym else MembershipPlan.objects.none()
    trainers = Trainer.objects.filter(gym=gym) if gym else Trainer.objects.none()

    if request.method == "POST":
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        name       = request.POST.get('name', '').strip()
        email      = request.POST.get('email', '').strip()
        phone      = request.POST.get('phone', '').strip()
        gender     = request.POST.get('gender')
        plan_id    = request.POST.get('plan')
        trainer_id = request.POST.get('trainer')
        reference  = request.POST.get('reference', '').strip()
        address    = request.POST.get('address', '').strip()
        confirm_transfer = request.POST.get('confirm_transfer') == '1'

        def fail(msg):
            if is_ajax:
                return JsonResponse({"error": msg}, status=400)
            messages.error(request, msg)
            return redirect('/enrollment/')

        selected_trainer = None
        if trainer_id:
            selected_trainer = Trainer.objects.filter(id=trainer_id, gym=gym).first()
            if not selected_trainer:
                return fail("Selected trainer does not exist.")

        selected_plan = MembershipPlan.objects.filter(id=plan_id, gym=gym).first()
        if not selected_plan:
            return fail("Selected plan does not exist.")
        if not confirm_transfer:
            other_enrollment = (
                Enrollment.objects
                .filter(user=request.user, is_active=True)
                .exclude(gym=gym)
                .select_related('gym')
                .order_by('-doj')
                .first()
            )
            if other_enrollment:
                payload = {
                    "transfer_check": True,
                    "existing": {
                        "old_enrollment_id":   other_enrollment.id,
                        "gym_name":            other_enrollment.gym.gym_name,
                        "member_id":           other_enrollment.unique_id,
                        "due_date":            other_enrollment.DueDate.strftime("%d %b %Y") if other_enrollment.DueDate else "—",
                        "pending_amount":      float(other_enrollment.pendingAmount),
                        "last_payment_amount": float(other_enrollment.paidAmount) if other_enrollment.paidAmount else 0,
                        "last_payment_date":   other_enrollment.paymentDate.strftime("%d %b %Y") if other_enrollment.paymentDate else None,
                    },
                }
                if is_ajax:
                    return JsonResponse(payload, status=200)
                messages.warning(
                    request,
                    f"You already have a membership at {other_enrollment.gym.gym_name}. "
                    "Please enable JavaScript to confirm the transfer, or contact support."
                )
                return redirect('/enrollment/')
        old_enrollment = None
        if confirm_transfer:
            old_enrollment_id = request.POST.get('old_enrollment_id')
            old_enrollment = (
                Enrollment.objects
                .filter(id=old_enrollment_id, user=request.user, is_active=True)
                .exclude(gym=gym)
                .select_related('gym')
                .first()
            )
        enroll = Enrollment(
            gym=gym,
            fullname=name,
            email=email,
            phone=phone,
            selectPlan=selected_plan,
            trainer=selected_trainer,
            gender=gender,
            reference=reference,
            address=address,
            user=request.user,
            paidAmount=0,
            pendingAmount=selected_plan.price,
            profile_completed=bool(email and gender and address),
        )
        enroll.save()
        if email and not request.user.email:
            request.user.email = email
            request.user.save(update_fields=['email'])

        if old_enrollment:
            try:
                with transaction.atomic():
                    EnrollmentTransfer.objects.create(
                        member=request.user,
                        mobile_number=phone,
                        previous_gym=old_enrollment.gym,
                        new_gym=gym,
                        previous_enrollment=old_enrollment,
                        previous_member_id=old_enrollment.unique_id,
                        previous_plan_name=old_enrollment.selectPlan.plan if old_enrollment.selectPlan else '',
                        previous_joining_date=old_enrollment.doj,
                        previous_due_date=old_enrollment.DueDate,
                        previous_pending_amount=old_enrollment.pendingAmount,
                        last_payment_amount=old_enrollment.paidAmount,
                        last_payment_date=old_enrollment.paymentDate,
                    )
            except IntegrityError:
                logger.info(
                    "Duplicate pending transfer skipped for enrollment_id=%s",
                    old_enrollment.id,
                )

        transaction.on_commit(lambda: notify_staff_new_enrollment(enroll))

        gym_pk = gym.pk if gym else 'none'
        cache.delete(f"enrollment_{request.user.id}_{gym_pk}")
        cache.delete(f"profile_image_{request.user.id}")
        cache.delete(f"enrolled_{request.user.id}_{gym_pk}")
        cache.delete(f"enrollment_status_{request.user.id}_{gym_pk}")

        if is_ajax:
            return JsonResponse({"redirect": "/profile/"})

        messages.success(request, "Welcome aboard! Your gym membership has been activated.")
        return redirect('/profile/')

    return render(request, 'enrollment.html', {"plans": plans, "trainers": trainers,})

@active_member_required
@login_required
def Profile(request):
    gym = getattr(request, 'gym', None)
    enrollment = request.enrollment

    plans_key = f"membership_plans_{gym.pk}" if gym else f"membership_plans_user_{request.user.id}"
    plans     = cache.get(plans_key)
    if plans is None:
        qs    = MembershipPlan.objects.filter(gym=gym) if gym else MembershipPlan.objects.none()
        plans = list(qs.values("id", "plan", "price", "duration_days"))
        cache.set(plans_key, plans, timeout=3600)

    image_url = None
    if enrollment and enrollment.face_image:
        image_url = cache.get(f"profile_image_{request.user.id}")
        if image_url is None:
            try:
                public_id = (
                    enrollment.face_image.public_id
                    if hasattr(enrollment.face_image, "public_id")
                    else str(enrollment.face_image)
                )
                if public_id:
                    image_url, _ = cloudinary_url(
                        public_id,
                        width=130, height=130,
                        crop="fill", gravity="face",
                        fetch_format="auto", quality="auto",
                        secure=True,
                    )
                    cache.set(f"profile_image_{request.user.id}", image_url, timeout=3600)
            except Exception:
                logger.exception("Cloudinary URL error for user %s", request.user.id)
    invoices = []
    if enrollment and enrollment.profile_completed:
        from billing.models import Invoice
        invoices = (
            Invoice.objects
            .filter(member=enrollment, status__in=Invoice.REVENUE_STATUSES)
            .order_by('-invoice_date', '-created_at')[:2]
        )
    return render(request, "profile.html", {
        "enrollment": enrollment,
        "image_url": image_url,
        "is_expired": enrollment.is_expired if enrollment else False,
        "days_remaining": enrollment.days_remaining if enrollment else 0,
        "plans": plans,
        "gym": gym,
        "invoices": invoices,
        "needs_profile_completion": bool(enrollment and not enrollment.profile_completed),
    })

@active_member_required
@login_required
def complete_profile(request):
    gym = getattr(request, 'gym', None)
    enrollment = get_object_or_404(Enrollment, user=request.user, gym=gym)

    if request.method == "POST":
        form = CompleteProfileForm(request.POST, request.FILES, instance=enrollment)
        if form.is_valid():
            obj = form.save(commit=False)

            required_filled = bool(obj.email and obj.address)
            obj.profile_completed = required_filled

            obj.save(update_fields=[
                'email', 'address', 'reference', 'profile_completed'
            ])

            cache.delete(f"enrollment_{request.user.id}_{gym.pk}")
            cache.delete(f"profile_image_{request.user.id}")

            if required_filled:
                messages.success(request, "Profile completed!")
            else:
                messages.info(request, "Progress saved. A few required fields are still missing.")
            return redirect('/profile/')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = CompleteProfileForm(instance=enrollment)

    return render(request, "complete_profile.html", {"form": form, "gym": gym, "enrollment": enrollment})

@active_member_required
@login_required
def upload_profile_pic(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    gym        = getattr(request, 'gym', None)
    enrollment = request.enrollment
    if not enrollment:
        messages.error(request, "You are not enrolled yet.")
        return redirect('/profile/')

    pic = request.FILES.get("profile_pic")
    if not pic:
        messages.error(request, "No image selected.")
        return redirect('/profile/')

    if enrollment.face_image:
        try:
            old_id = (
                enrollment.face_image.public_id
                if hasattr(enrollment.face_image, "public_id")
                else str(enrollment.face_image)
            )
            if old_id:
                cloudinary.uploader.destroy(old_id)
        except Exception:
            pass

    try:
        img = Image.open(pic)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        max_side = 800
        w, h     = img.size
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            img   = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buffer  = io.BytesIO()
        quality = 85
        while quality >= 30:
            buffer.seek(0); buffer.truncate()
            img.save(buffer, format="JPEG", optimize=True, quality=quality)
            if buffer.tell() / 1024 <= 100:
                break
            quality -= 10
        buffer.seek(0)
    except Exception as e:
        messages.error(request, f"Image processing failed: {e}")
        return redirect('/profile/')

    try:
        result    = cloudinary.uploader.upload(buffer, folder="profile_pics", resource_type="image")
        public_id = result["public_id"]
        enrollment.face_image = public_id
        enrollment.save(update_fields=["face_image"])
        cache.delete(f"profile_image_{request.user.id}")
        gym_pk = gym.pk if gym else 'none'
        cache.delete(f"enrollment_{request.user.id}_{gym_pk}")
        messages.success(request, "Profile picture updated successfully!")
    except Exception as e:
        messages.error(request, f"Upload failed: {e}")

    return redirect('/profile/')

@active_member_required
@login_required
def attendance_page(request):
    gym        = getattr(request, 'gym', None)
    enrollment = request.enrollment
    if not enrollment:
        return redirect('/enrollment/')
    geo_enabled = bool(gym and gym.enable_geo_attendance)
    today = timezone.localdate()
    user  = request.user
    already_mark = Attendence_model.objects.filter(
        user=user, date=today, gym=gym
    ).exists()

    all_attended = list(
        Attendence_model.objects
        .filter(user=user, gym=gym)
        .order_by('-date')
    )
    return render(request, "attendence.html", {
        "enrollment":   enrollment,
        "records":      all_attended[:30],
        "already_mark": already_mark,
        "attended":     all_attended[:7],
        "total_days":   len(all_attended),
        "monthly_days": sum(
            1 for a in all_attended
            if a.date.year == today.year and a.date.month == today.month
        ),
        "today": today,
        "gym" : gym,
        "geo_enabled": geo_enabled,
    })
@active_member_required
@login_required
@require_POST
def renew_membership(request):
    gym        = getattr(request, 'gym', None)
    gym_pk     = gym.pk if gym else 'none'
    enrollment = request.enrollment

    plan_id       = request.POST.get("plan")
    selected_plan = MembershipPlan.objects.filter(id=plan_id, gym=gym).first()
    if not selected_plan:
        messages.error(request, "Invalid plan selected.")
        return redirect('/profile/')

    today = timezone.now().date()
    if enrollment.DueDate and enrollment.DueDate > today:
        new_due_date = enrollment.DueDate + timedelta(days=selected_plan.duration_days)
    else:
        new_due_date = today + timedelta(days=selected_plan.duration_days)

    enrollment.selectPlan    = selected_plan
    enrollment.Amount        = selected_plan.price
    enrollment.paidAmount    = 0
    enrollment.pendingAmount = selected_plan.price
    enrollment.paymentStatus = "Pending"
    enrollment.paymentMethod = None
    enrollment.paymentDate   = None
    enrollment.DueDate       = new_due_date
    enrollment.save(update_fields=[
        "selectPlan", "Amount", "paidAmount", "pendingAmount",
        "paymentStatus", "paymentMethod", "paymentDate", "DueDate",
    ])

    cache.delete(f"enrollment_{request.user.id}_{gym_pk}")
    cache.delete(f"enrollment_status_{request.user.id}_{gym_pk}")
    cache.delete(f"admin_revenue_{gym_pk}")

    messages.success(request, f"Membership renewed with {selected_plan.plan}! Please complete payment.")
    return redirect('/profile/')

@_gym_staff_required
@require_POST
def staff_renew_membership(request, member_id):
    gym = getattr(request, 'gym', None)
    gym_pk = gym.pk if gym else 'none'

    enrollment = get_object_or_404(
        get_member_detail_queryset(gym), pk=member_id
    )

    plan_id = request.POST.get("plan")
    selected_plan = MembershipPlan.objects.filter(id=plan_id, gym=gym).first()
    if not selected_plan:
        messages.error(request, "Invalid plan selected.")
        return redirect('member_detail', member_id=member_id)
    old_plan = enrollment.selectPlan
    old_price = enrollment.Amount
    old_due_date = enrollment.DueDate

    today = timezone.now().date()
    if enrollment.DueDate and enrollment.DueDate > today:
        new_due_date = enrollment.DueDate + timedelta(days=selected_plan.duration_days)
    else:
        new_due_date = today + timedelta(days=selected_plan.duration_days)

    enrollment.selectPlan    = selected_plan
    enrollment.Amount        = selected_plan.price
    enrollment.paidAmount    = 0
    enrollment.pendingAmount = selected_plan.price
    enrollment.paymentStatus = "Pending"
    enrollment.paymentMethod = None
    enrollment.paymentDate   = None
    enrollment.DueDate       = new_due_date
    enrollment.save(update_fields=[
        "selectPlan", "Amount", "paidAmount", "pendingAmount",
        "paymentStatus", "paymentMethod", "paymentDate", "DueDate",
    ])
    MembershipPlanChangeLog.objects.create(
        gym=gym,
        enrollment=enrollment,
        old_plan=old_plan,
        new_plan=selected_plan,
        old_price=old_price,
        new_price=selected_plan.price,
        old_due_date=old_due_date,
        new_due_date=new_due_date,
        reason="Renewal",
        changed_by=request.user,
    )

    from AuthFit.audit import log_action
    log_action(
        gym=gym,
        action='membership_renewed',
        staff_user=request.user,
        request=request,
        object_type='Enrollment',
        object_id=enrollment.pk,
        object_label=enrollment.fullname,
        old_values={'plan': old_plan.plan if old_plan else None, 'due_date': str(old_due_date)},
        new_values={'plan': selected_plan.plan, 'due_date': str(new_due_date)},
    )
    if enrollment.user_id:
        cache.delete(f"enrollment_{enrollment.user_id}_{gym_pk}")
        cache.delete(f"enrollment_status_{enrollment.user_id}_{gym_pk}")
    cache.delete(f"admin_revenue_{gym_pk}")
    
    transaction.on_commit(lambda: notify_member_plan_changed(
        enrollment,
        selected_plan,
        new_due_date=new_due_date,
        pending_amount=enrollment.pendingAmount,
    ))

    messages.success(request, f"{enrollment.fullname}'s membership renewed with {selected_plan.plan}.")
    return redirect('member_detail', member_id=member_id)


@_gym_staff_required
@require_POST
def staff_edit_member(request, member_id):
    gym = getattr(request, 'gym', None)
    gym_pk = gym.pk if gym else 'none'

    enrollment = get_object_or_404(
        get_member_detail_queryset(gym), pk=member_id
    )

    fullname = request.POST.get("fullname", "").strip()
    gender = request.POST.get("gender", "").strip()
    address = request.POST.get("address", "").strip()
    start_date_raw = request.POST.get("membership_start_date", "").strip()

    if not fullname:
        messages.error(request, "Name is required.")
        return redirect('member_detail', member_id=member_id)

    if gender not in ("M", "F", ""):
        messages.error(request, "Select a valid gender.")
        return redirect('member_detail', member_id=member_id)

    try:
        new_start_date = date.fromisoformat(start_date_raw) if start_date_raw else enrollment.membership_start_date
    except ValueError:
        messages.error(request, "Invalid start date.")
        return redirect('member_detail', member_id=member_id)

    start_date_changed = new_start_date != enrollment.membership_start_date

    enrollment.fullname = fullname
    enrollment.gender = gender or None
    enrollment.address = address
    enrollment.membership_start_date = new_start_date

    update_fields = ["fullname", "gender", "address", "membership_start_date"]
    if start_date_changed and enrollment.selectPlan and enrollment.selectPlan.duration_days:
        enrollment.DueDate = new_start_date + timedelta(days=enrollment.selectPlan.duration_days)
        update_fields.append("DueDate")

    enrollment.save(update_fields=update_fields)

    if enrollment.user_id:
        cache.delete(f"enrollment_{enrollment.user_id}_{gym_pk}")
        cache.delete(f"enrollment_status_{enrollment.user_id}_{gym_pk}")

    messages.success(request, f"{enrollment.fullname}'s details updated.")
    return redirect('member_detail', member_id=member_id)

def _panel_data(e, kind, expired_days=None):
    return {
        "unique_id": e.unique_id,
        "kind": kind,
        "name": e.fullname,
        "phone": e.phone,
        "gender": getattr(e, "gender", None),
        "email": getattr(e, "email", None),
        "address": getattr(e, "address", None),
        "gym_name": _gym_name(e),
        "plan": e.selectPlan.plan if e.selectPlan else None,
        "plan_price": getattr(e.selectPlan, "price", None) if e.selectPlan else None,
        "trainer": e.trainer.name if getattr(e, "trainer", None) else None,
        "doj": e.doj.strftime('%d %b %Y') if getattr(e, "doj", None) else None,
        "payment_status": e.paymentStatus,
        "pending_amount": float(e.pendingAmount) if e.pendingAmount else 0,
        "amount": str(e.pendingAmount) if e.pendingAmount else None,
        "due_date": e.DueDate.strftime('%d %b %Y') if e.DueDate else None,
        "days_remaining": e.days_remaining if hasattr(e, "days_remaining") else None,
        "expired_days": expired_days,
    }
 
@_gym_staff_required
def whatsapp_pending_users(request):
    gym = getattr(request, 'gym', None)
    today = timezone.now().date()

    base_qs = Enrollment.objects.select_related("selectPlan", "gym", "trainer").filter(is_deleted=False)
    if gym:
        base_qs = base_qs.filter(gym=gym)

    # ── PANEL 1: Due Payments ──
    pending_qs = base_qs.filter(paymentStatus="Pending").order_by("DueDate")

    pending_with_links = []
    for e in pending_qs:
        pending_with_links.append({
            "enrollment": e,
            "panel_data": _panel_data(e, "due"),
        })
    expiring_with_links = []
    for e in base_qs:
        days_left = getattr(e, "days_remaining", None)
        if days_left is not None and 0 <= days_left <= 7:
            expiring_with_links.append({
                "enrollment": e,
                "panel_data": _panel_data(e, "expiring"),
            })
    expiring_with_links.sort(key=lambda item: item["panel_data"]["days_remaining"])
    expired_with_links = []
    for e in base_qs:
        days_left = getattr(e, "days_remaining", None)
        if days_left is not None and days_left < 0:
            expired_days = abs(days_left)
            expired_with_links.append({
                "enrollment": e,
                "expired_days": expired_days,
                "panel_data": _panel_data(e, "expired", expired_days=expired_days),
            })
    expired_with_links.sort(key=lambda item: -item["expired_days"])

    return render(request, "admin_whatsapp.html", {
        "pending": pending_with_links,
        "expiring_soon": expiring_with_links,
        "expired_members": expired_with_links,
        "pending_count": len(pending_with_links),
        "expiring_count": len(expiring_with_links),
        "expired_count": len(expired_with_links),
        "templates_json": json.dumps(MESSAGE_TEMPLATES),
        "gym": gym,
    })
 

@_gym_staff_required
def payment_management(request):
    gym           = getattr(request, 'gym', None)
    status_filter = request.GET.get("filter", "pending")
    since         = timezone.now() - timedelta(days=7)
    METHOD_LABELS = {"C": "Cash", "U": "UPI", "B": "UPI + Cash"}

    search_ctx = get_search_context(request)

    qs = Enrollment.objects.select_related("selectPlan", "trainer").filter(is_deleted=False)
    if gym:
        qs = qs.filter(gym=gym)

    if status_filter == "done":
        qs = qs.filter(created_at__gte=since, paymentStatus="Done")
    else:
        qs = qs.filter(paymentStatus="Pending")

    qs = apply_search(qs, search_ctx["search_by"], search_ctx["search"])
    qs = qs.order_by("-created_at")
    page_number = request.GET.get("page", 1)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(page_number)
    rows = [
        {
            "id":                   e.id,
            "unique_id":            e.unique_id,
            "fullname":             e.fullname,
            "phone":                e.phone,
            "plan_name":            e.selectPlan.plan if e.selectPlan else "—",
            "plan_price":           float(e.selectPlan.price) if e.selectPlan else 0,
            "amount":               float(e.Amount),
            "paid":                 float(e.paidAmount),
            "pending":              float(e.pendingAmount),
            "payment_status":       e.paymentStatus,
            "payment_method":       e.paymentMethod or "",
            "payment_method_label": METHOD_LABELS.get(e.paymentMethod, "—"),
            "payment_date":         e.paymentDate.strftime("%Y-%m-%d") if e.paymentDate else "",
            "doj":                  e.doj.strftime("%d %b %Y") if e.doj else "—",
            "due_date":             e.DueDate.strftime("%b. %d, %Y") if e.DueDate else "—",
            "days_remaining":       e.days_remaining,
            "is_expired":           e.is_expired,
        }
        for e in page_obj
    ]
    base_qs       = Enrollment.objects.filter(is_deleted=False)
    if gym:
        base_qs = base_qs.filter(gym=gym)
    pending_count = base_qs.filter(paymentStatus="Pending").count()
    paid_count    = base_qs.filter(created_at__gte=since, paymentStatus="Done").count()

    return render(request, "payment_management.html", {
        "rows":                 rows,
        "page_obj":             page_obj,
        "status_filter":        status_filter,
        "total_pending_amount": sum(r["pending"] for r in rows),
        "total_count":          len(rows),
        "pending_count":        pending_count,
        "paid_count":           paid_count,
        "gym":                  gym, **search_ctx,
        "extra_params_list": [("filter", status_filter)],
        **search_ctx,
    })


@_gym_staff_required
@require_POST
def update_payment(request):
    gym = getattr(request, 'gym', None)
    try:
        data           = json.loads(request.body)
        enrollment_id  = int(data.get("enrollment_id", 0))
        paid_amount    = float(data.get("paid_amount", 0))
        payment_method = data.get("payment_method", "").strip()
        payment_date_s = data.get("payment_date", "").strip() or None

        if paid_amount < 0:
            return JsonResponse({"error": "Paid amount cannot be negative."}, status=400)
        if payment_method not in ("C", "U", "B", ""):
            return JsonResponse({"error": "Invalid payment method."}, status=400)

        qs = Enrollment.objects.select_related("selectPlan", "user")
        if gym:
            qs = qs.filter(gym=gym)
        enrollment = qs.get(pk=enrollment_id)

        plan_price     = float(enrollment.selectPlan.price) if enrollment.selectPlan else float(enrollment.Amount)
        paid_amount    = min(paid_amount, plan_price)
        pending_amount = max(plan_price - paid_amount, 0)

        amount_paid_now = paid_amount - float(enrollment.paidAmount)

        enrollment.paidAmount    = paid_amount
        enrollment.pendingAmount = pending_amount
        enrollment.paymentStatus = "Done" if pending_amount == 0 else "Pending"
        enrollment.paymentMethod = payment_method or None

        if payment_date_s:
            enrollment.paymentDate = date.fromisoformat(payment_date_s)
        elif paid_amount > 0 and not enrollment.paymentDate:
            enrollment.paymentDate = timezone.localdate()

        enrollment.save(update_fields=[
            "paidAmount", "pendingAmount", "paymentStatus",
            "paymentMethod", "paymentDate",
        ])
        from billing.models import Payment
        from billing.services.invoice_generator import create_invoice_for_payment
        from billing.services.pdf_generator import generate_invoice_pdf

        if amount_paid_now > 0:    # ← changed from `if paid_amount > 0:`
            payment = Payment.objects.create(
                gym=gym,
                enrollment=enrollment,
                member_name=enrollment.fullname,
                member_phone=enrollment.phone,
                member_unique_id=enrollment.unique_id,
                plan_name=enrollment.selectPlan.plan if enrollment.selectPlan else '',
                plan_duration_days=enrollment.selectPlan.duration_days if enrollment.selectPlan else 30,
                amount=plan_price,
                paid_amount=amount_paid_now,    # ← changed from `paid_amount`
                pending_amount=pending_amount,
                payment_method=payment_method or None,
                payment_date=enrollment.paymentDate or timezone.localdate(),
                membership_start=enrollment.doj,
                membership_end=enrollment.DueDate,
            )
            invoice = create_invoice_for_payment(payment)
            try:
                generate_invoice_pdf(invoice)
            except Exception:
                logger.exception("PDF generation failed for invoice %s", invoice.invoice_number)
        uid = enrollment.user_id
        gp  = gym.pk if gym else 'none'
        cache.delete(f"admin_revenue_{gp}")
        cache.delete(f"enrollment_{uid}_{gp}")
        cache.delete(f"enrollment_status_{uid}_{gp}")
        if amount_paid_now > 0:
            from AuthFit.audit import log_action
            log_action(
                gym=gym,
                action='payment_received',
                staff_user=request.user,
                request=request,
                object_type='Enrollment',
                object_id=enrollment.pk,
                object_label=enrollment.fullname,
                new_values={
                    'amount_paid_now': str(amount_paid_now),
                    'payment_method': payment_method,
                    'total_paid': str(paid_amount),
                },
            )
        METHOD_LABELS = {"C": "Cash", "U": "UPI", "B": "UPI + Cash"}
        return JsonResponse({
            "ok":                   True,
            "enrollment_id":        enrollment.id,
            "paid":                 float(enrollment.paidAmount),
            "pending":              float(enrollment.pendingAmount),
            "payment_status":       enrollment.paymentStatus,
            "payment_method_label": METHOD_LABELS.get(enrollment.paymentMethod, "—"),
            "payment_date":         enrollment.paymentDate.strftime("%d %b %Y") if enrollment.paymentDate else "—",
        })

    except Enrollment.DoesNotExist:
        return JsonResponse({"error": "Enrollment not found."}, status=404)
    except (ValueError, KeyError) as e:
        return JsonResponse({"error": f"Invalid data: {e}"}, status=400)
    except Exception:
        logger.exception("Error in update_payment")
        return JsonResponse({"error": "Internal error."}, status=500)

@login_required
@require_GET
def invoice_pdf_view(request, pk):
    from django.shortcuts import redirect
 
    gym     = _gym_from_request(request)
    invoice = get_object_or_404(Invoice, pk=pk, gym=gym)
 
    if not invoice.pdf_url:
        generate_invoice_pdf(invoice)
 
    return redirect(invoice.pdf_url)
 
@login_required
@require_GET
def invoice_pdf_regenerate_view(request, pk):
    gym     = _gym_from_request(request)
    invoice = get_object_or_404(Invoice, pk=pk, gym=gym)
 
    try:
        url = generate_invoice_pdf(invoice)
        return JsonResponse({'ok': True, 'pdf_url': url})
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)
 
@login_required
@require_GET
def gstr1_export_view(request):
    gym = _gym_from_request(request)
    if gym is None:
        return HttpResponse('Gym not found', status=404)
 
    today = date.today()
    if today.month >= 4:
        fy_start = date(today.year, 4, 1)
    else:
        fy_start = date(today.year - 1, 4, 1)
 
    try:
        start_date = datetime.strptime(request.GET.get('from', fy_start.isoformat()), '%Y-%m-%d').date()
        end_date   = datetime.strptime(request.GET.get('to',   today.isoformat()),    '%Y-%m-%d').date()
    except ValueError:
        return HttpResponse('Invalid date format. Use YYYY-MM-DD.', status=400)
 
    buf = generate_gstr1_style_report(gym, start_date, end_date)
 
    fy_label = f"{start_date.year}-{str(start_date.year + 1)[-2:]}"
    filename = f"GSTR1_{gym.gym_code}_{fy_label}.xlsx"

    try:
        buf.seek(0)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.xlsx')
        with os.fdopen(tmp_fd, 'wb') as tmp_file:
            tmp_file.write(buf.read())
 
        key = f"reports/{gym.gym_code}/{fy_label}/{filename}"
        upload_file_to_r2(
            tmp_path,
            key,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    except Exception:
        logger.exception("Failed to save GSTR-1 report to R2 for gym=%s", gym.gym_code)
    finally:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)

    buf.seek(0)
    response = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
 

@login_required
@require_POST
def create_payment_view(request):
    from AuthFit.models import Enrollment
 
    gym = _gym_from_request(request)
    if gym is None:
        return JsonResponse({'ok': False, 'error': 'Gym not found'}, status=404)
 
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
 
    enrollment_id = body.get('enrollment_id')
    paid_amount   = body.get('paid_amount')
    method        = body.get('payment_method', 'C')
    payment_date_str = body.get('payment_date', date.today().isoformat())
 
    if not enrollment_id or not paid_amount:
        return JsonResponse({'ok': False, 'error': 'enrollment_id and paid_amount are required'}, status=400)
 
    try:
        enrollment = Enrollment.objects.get(pk=enrollment_id, gym=gym)
    except Enrollment.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Enrollment not found'}, status=404)
 
    try:
        payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Invalid payment_date. Use YYYY-MM-DD.'}, status=400)
 
    from decimal import Decimal, InvalidOperation
    try:
        paid_decimal = Decimal(str(paid_amount))
    except InvalidOperation:
        return JsonResponse({'ok': False, 'error': 'Invalid paid_amount'}, status=400)
 
    payment = Payment.objects.create(
        gym             = gym,
        enrollment      = enrollment,
        member_name     = enrollment.fullname,
        member_phone    = enrollment.phone,
        member_unique_id = enrollment.unique_id,
        plan_name       = enrollment.selectPlan.plan,
        plan_duration_days = enrollment.selectPlan.duration_days,
        amount          = enrollment.Amount,
        paid_amount     = paid_decimal,
        pending_amount  = max(Decimal('0'), enrollment.pendingAmount - paid_decimal),
        payment_method  = method,
        payment_date    = payment_date,
        membership_start = enrollment.doj,
        membership_end   = enrollment.DueDate,
    )
 
    invoice = create_invoice_for_payment(payment)
    try:
        generate_invoice_pdf(invoice)
    except Exception as exc:
        pass
    from AuthFit.audit import log_action
    log_action(
        gym=gym,
        action='payment_received',
        staff_user=request.user,
        request=request,
        object_type='Enrollment',
        object_id=enrollment.pk,
        object_label=enrollment.fullname,
        new_values={'amount': str(paid_decimal), 'invoice_number': invoice.invoice_number},
    )
    return JsonResponse({
        'ok': True,
        'invoice_number': invoice.invoice_number,
        'pdf_url': invoice.pdf_url or '',
        'grand_total': str(invoice.grand_total),
    })

def today_attendance(request):
    gym   = getattr(request, 'gym', None)
    today = timezone.localdate()
    search_ctx = get_search_context(request)
    search_by, search = search_ctx["search_by"], search_ctx["search"]
    current_hour = timezone.localtime().hour
    default_section = "Evening" if current_hour >= 16 else "Morning"
    cache_key = f"today_attendance_{gym.pk if gym else 'super'}_{today}_{default_section}"
    ai_wallet = get_or_create_wallet(gym) if gym else None
    ai_credit_ctx = {
        "ai_credits_balance": ai_wallet.balance if ai_wallet else None,
        "ai_credits_low": bool(ai_wallet and 0 < ai_wallet.balance <= 3),
        "ai_credits_zero": bool(ai_wallet and ai_wallet.balance == 0),
    }

    if not search:
        cached = cache.get(cache_key)
        if cached:
            cached.update(ai_credit_ctx)
            return render(request, "today_attendance.html", cached)

    qs = (
        Attendence_model.objects
        .filter(date=today)
        .select_related("user", "enrollment", "enrollment__selectPlan", "enrollment__trainer")
    )
    if gym:
        qs = qs.filter(gym=gym)

    qs = apply_related_search(
        qs, search_by, search,
        relation_prefix="user__enrollment", gym=gym,
    )
    qs = qs.order_by("timestamp")

    morning, evening = [], []
    alerts = []
    EXPIRING_SOON_THRESHOLD = 3

    for rec in qs:
        enrollment = rec.enrollment
        image_url  = None
        local_ts = timezone.localtime(rec.timestamp)
        if enrollment and enrollment.face_image:
            try:
                public_id = (
                    enrollment.face_image.public_id
                    if hasattr(enrollment.face_image, "public_id")
                    else str(enrollment.face_image)
                )
                if public_id:
                    image_url, _ = cloudinary_url(
                        public_id, width=60, height=60,
                        crop="fill", gravity="face",
                        fetch_format="auto", quality="auto", secure=True,
                    )
            except Exception:
                logger.exception("Cloudinary URL error for user %s", rec.user.id)

        pending_amount = float(enrollment.pendingAmount) if enrollment else 0
        is_expired     = enrollment.is_expired if enrollment else False
        days_remaining = enrollment.days_remaining if enrollment else None
        is_expiring_soon = (
            not is_expired and days_remaining is not None
            and days_remaining <= EXPIRING_SOON_THRESHOLD
        )
        has_pending = pending_amount > 0

        if is_expired:
            alert_rank = 0
        elif is_expiring_soon:
            alert_rank = 1
        elif has_pending:
            alert_rank = 2
        else:
            alert_rank = None

        entry = {
            "id":               rec.id,
            "time": local_ts.strftime("%I:%M %p"),
            "name":             enrollment.fullname if enrollment else rec.user.username,
            "unique_id":        enrollment.unique_id if enrollment else "—",
            "image_url":        image_url,
            "pending_amount":   pending_amount,
            "due_date":         enrollment.DueDate.strftime("%d %b %Y") if enrollment and enrollment.DueDate else "—",
            "is_expired":       is_expired,
            "is_expiring_soon": is_expiring_soon,
            "has_pending":      has_pending,
            "alert_rank":       alert_rank,
            "phone":            enrollment.phone if enrollment else "—",
            "address":          enrollment.address if enrollment else "—",
            "plan":             enrollment.selectPlan.plan if enrollment and enrollment.selectPlan else "—",
            "plan_price":       float(enrollment.selectPlan.price) if enrollment and enrollment.selectPlan else 0,
            "trainer":          enrollment.trainer.name if enrollment and enrollment.trainer else "No Trainer",
            "gender":           enrollment.get_gender_display() if enrollment else "—",
            "doj":              enrollment.doj.strftime("%d %b %Y") if enrollment and enrollment.doj else "—",
            "payment_status":   enrollment.paymentStatus if enrollment else "—",
            "days_remaining":   days_remaining,
            "payment_date":     enrollment.paymentDate.strftime("%d %b %Y") if enrollment and enrollment.paymentDate else "—",
        }

        (morning if local_ts.hour < 14 else evening).append(entry)
        if alert_rank is not None:
            alerts.append(entry)
    alerts.sort(key=lambda e: (e["alert_rank"], -e["pending_amount"]))

    previous_days = _get_previous_days_attendance(gym, today, days=3)
    previous_days_json = json.dumps(
        [{"label": d["label"], "records": d["records"]} for d in previous_days]
    )
    context = {
        "sections":     [("Morning", "🌅", morning), ("Evening", "🌆", evening)],
        "alerts":       alerts,
        "alerts_count": len(alerts),
        "today":        today,
        "total":        len(morning) + len(evening),
        "gym":          gym,
        "previous_days": previous_days,
        "previous_days_json": previous_days_json,
        "default_section": default_section,
        "extra_params_list": [],
        **search_ctx,
        **ai_credit_ctx,   # NEW
    }
    if not search:
        cache.set(cache_key, context, timeout=120)
    return render(request, "today_attendance.html", context)

@_gym_staff_required
def freeze_membership(request):
    gym = getattr(request, 'gym', None)
    from AuthFit.permissions import has_permission

    active_tab = request.GET.get("tab", "freeze")
    if active_tab not in ("freeze", "changeplan", "deleteenrollment"):
        active_tab = "freeze"
    freeze_query = request.GET.get("freeze_search", "").strip()
    qs = Enrollment.objects.filter(is_deleted=False).select_related("selectPlan").order_by("fullname")
    if gym:
        qs = qs.filter(gym=gym)
    if freeze_query:
        qs = qs.filter(unique_id=freeze_query)
    paginator = Paginator(qs, 20)
    page_obj  = paginator.get_page(request.GET.get("page", 1))

    can_change_plan = has_permission(request, "can_change_membership_plan")
    can_delete_enrollment = has_permission(request, "can_delete_enrollment")
    plans = []
    change_plan_rows = []
    cmp_page_obj = None
    cmp_query = ""
    if can_change_plan and gym:
        plans = list(
            MembershipPlan.objects.filter(gym=gym)
            .order_by('price')
            .values('id', 'plan', 'price', 'duration_days')
        )

        cmp_query = request.GET.get("cmp_search", "").strip()
        cmp_qs = (
            Enrollment.objects.filter(gym=gym, is_deleted=False)
            .select_related("selectPlan")
            .order_by("fullname")
        )
        if cmp_query:
            cmp_qs = cmp_qs.filter(
                Q(fullname__icontains=cmp_query) |
                Q(phone__icontains=cmp_query) |
                Q(unique_id__icontains=cmp_query)
            )

        cmp_paginator = Paginator(cmp_qs, 20)
        cmp_page_obj  = cmp_paginator.get_page(request.GET.get("cmp_page", 1))

        member_ids = [e.id for e in cmp_page_obj.object_list]
        invoice_map = {}
        invoices_qs = (
            Invoice.objects
            .filter(gym=gym, member_id__in=member_ids, status__in=Invoice.REVENUE_STATUSES)
            .order_by('member_id', '-invoice_date', '-created_at')
        )
        for inv in invoices_qs:
            invoice_map.setdefault(inv.member_id, inv.invoice_number)

        for e in cmp_page_obj.object_list:
            change_plan_rows.append({
                "id": e.id,
                "unique_id": e.unique_id,
                "fullname": e.fullname,
                "phone": e.phone,
                "current_plan_id": e.selectPlan_id,
                "current_plan_name": e.selectPlan.plan if e.selectPlan else "—",
                "current_price": float(e.Amount),
                "current_duration_days": e.selectPlan.duration_days if e.selectPlan else 0,
                "payment_status": e.paymentStatus,
                "paid_amount": float(e.paidAmount),
                "pending_amount": float(e.pendingAmount),
                "due_date_display": e.DueDate.strftime("%d %b %Y") if e.DueDate else "—",
                "invoice_number": invoice_map.get(e.id, "—"),
            })
    del_page_obj = None
    del_query = ""
    del_status = "all"
    if can_delete_enrollment and gym:
        del_query, del_status, del_page_obj = _build_delete_enrollment_page(request, gym)

    return render(request, "freeze_membership.html", {
        "active_tab": active_tab,

        "page_obj": page_obj,
        "freeze_search_by_choices": [("id", "Member ID")],
        "freeze_search_by": "id",
        "freeze_search": freeze_query,

        "can_change_plan": can_change_plan,
        "can_delete_enrollment": can_delete_enrollment,
        "plans": plans,
        "change_plan_rows": change_plan_rows,
        "cmp_page_obj": cmp_page_obj,
        "cmp_query": cmp_query,
        "cmp_search_by_choices": [("all", "Name / Phone / ID")],
        "cmp_search_by": "all",

        "del_page_obj": del_page_obj,
        "del_query": del_query,
        "del_status": del_status,
        "del_search_by_choices": [("all", "Name / Phone / ID")],
        "del_search_by": "all",
        "del_status_choices": [
            ("all", "All"),
            ("active", "Active"),
            ("frozen", "Frozen"),
            ("expired", "Expired"),
            ("pending_signup", "Pending Signup"),
            ("duplicate", "Duplicate Candidates"),
        ],
    })


def _build_delete_enrollment_page(request, gym):
    from django.db.models import Count
    from datetime import timedelta

    today = timezone.localdate()

    del_query  = request.GET.get("del_search", "").strip()
    del_status = request.GET.get("dstatus", "all").strip() or "all"

    base_qs = (
        Enrollment.objects
        .filter(gym=gym, is_deleted=False)
        .select_related("selectPlan", "trainer")
        .order_by("-doj")
    )

    if del_query:
        base_qs = base_qs.filter(
            Q(fullname__icontains=del_query) |
            Q(phone__icontains=del_query) |
            Q(unique_id__icontains=del_query)
        )

    if del_status == "active":
        base_qs = base_qs.filter(user__isnull=False, DueDate__gte=today)
    elif del_status == "frozen":
        base_qs = base_qs.filter(user__isnull=False, DueDate__gt=today + timedelta(days=5))
    elif del_status == "expired":
        base_qs = base_qs.filter(DueDate__lt=today)
    elif del_status == "pending_signup":
        base_qs = base_qs.filter(user__isnull=True)
    elif del_status == "duplicate":
        dup_phones = (
            Enrollment.objects
            .filter(gym=gym, is_deleted=False)
            .values("phone")
            .annotate(cnt=Count("id"))
            .filter(cnt__gt=1)
            .values_list("phone", flat=True)
        )
        base_qs = base_qs.filter(phone__in=list(dup_phones))

    paginator = Paginator(base_qs, 20)
    page_obj  = paginator.get_page(request.GET.get("dpage", 1))

    phones_on_page = {e.phone for e in page_obj.object_list}
    names_on_page  = {e.fullname.strip().lower() for e in page_obj.object_list}

    dup_phone_counts = dict(
        Enrollment.objects.filter(gym=gym, is_deleted=False, phone__in=phones_on_page)
        .values("phone").annotate(cnt=Count("id")).values_list("phone", "cnt")
    )
    dup_name_counts = {}
    if names_on_page:
        from django.db.models.functions import Lower
        dup_name_counts = dict(
            Enrollment.objects.filter(gym=gym, is_deleted=False)
            .annotate(lname=Lower("fullname"))
            .filter(lname__in=names_on_page)
            .values("lname").annotate(cnt=Count("id")).values_list("lname", "cnt")
        )

    for e in page_obj.object_list:
        is_dup_phone = dup_phone_counts.get(e.phone, 0) > 1
        is_dup_name  = dup_name_counts.get(e.fullname.strip().lower(), 0) > 1
        e.is_possible_duplicate = is_dup_phone or is_dup_name

    return del_query, del_status, page_obj

@permission_required("can_change_membership_plan")
@require_POST
def change_membership_plan_view(request, enrollment_id):
    gym = getattr(request, 'gym', None)
    if gym is None:
        return JsonResponse({"success": False, "error": "No gym context available."}, status=403)
 
    enrollment = (
        Enrollment.objects.select_related('selectPlan')
        .filter(gym=gym, pk=enrollment_id)
        .first()
    )
    if not enrollment:
        return JsonResponse({"success": False, "error": "Member not found."}, status=404)
 
    new_plan_id   = request.POST.get('new_plan_id', '').strip()
    reason        = request.POST.get('reason', '').strip()
    effective_raw = request.POST.get('effective_date', '').strip()
    confirmed     = request.POST.get('confirm') in ('1', 'on', 'true')
 
    if not confirmed:
        return JsonResponse({
            "success": False,
            "error": "Please confirm you understand this will update the member's invoice.",
        }, status=400)
 
    new_plan = MembershipPlan.objects.filter(pk=new_plan_id, gym=gym).first()
    if not new_plan:
        return JsonResponse({"success": False, "error": "Selected plan is invalid for this gym."}, status=400)
 
    if effective_raw:
        try:
            effective_date = date.fromisoformat(effective_raw)
        except ValueError:
            return JsonResponse({"success": False, "error": "Invalid effective date."}, status=400)
    else:
        effective_date = timezone.localdate()
 
    try:
        result = change_membership_plan(
            enrollment, new_plan, effective_date, reason, request.user,
        )
    except PlanChangeError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except Exception:
        logger.exception(
            "change_membership_plan failed for enrollment_id=%s", enrollment.id
        )
        return JsonResponse({
            "success": False,
            "error": "Something went wrong updating the membership. No changes were saved.",
        }, status=500)

    return JsonResponse({"success": True, **result})

@permission_required("can_delete_enrollment")
@require_POST
def delete_enrollment_view(request, enrollment_id):
    gym = getattr(request, 'gym', None)
    if gym is None:
        return JsonResponse({"success": False, "error": "No gym context available."}, status=403)

    enrollment = Enrollment.objects.filter(gym=gym, pk=enrollment_id).first()
    if not enrollment:
        return JsonResponse({"success": False, "error": "Member not found."}, status=404)

    delete_type  = request.POST.get('delete_type', '').strip()
    reason       = request.POST.get('reason', '').strip()
    confirm_text = request.POST.get('confirm_text', '').strip()

    if confirm_text != 'DELETE':
        return JsonResponse({"success": False, "error": "Type DELETE to confirm."}, status=400)

    if delete_type not in ('duplicate', 'soft'):
        return JsonResponse({"success": False, "error": "Invalid delete type."}, status=400)

    from AuthFit.services.delete_enrollment import (
        delete_enrollment_duplicate, delete_enrollment_soft, DeleteEnrollmentError,
    )
    enrollment_label = enrollment.fullname 
    try:
        if delete_type == 'duplicate':
            delete_enrollment_duplicate(enrollment, gym, request.user, reason)
            msg = "Duplicate enrollment deleted successfully."
        else:
            delete_enrollment_soft(enrollment, gym, request.user, reason)
            msg = "Enrollment removed successfully."
    except DeleteEnrollmentError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=403)
    except Exception:
        logger.exception("delete_enrollment_view failed for enrollment_id=%s", enrollment_id)
        return JsonResponse({
            "success": False,
            "error": "Something went wrong while deleting. No changes were saved.",
        }, status=500)

    from AuthFit.audit import log_action
    log_action(
        gym=gym,
        action='enrollment_deleted',
        staff_user=request.user,
        request=request,
        object_type='Enrollment',
        object_id=enrollment_id,
        object_label=enrollment_label,
        new_values={'delete_type': delete_type, 'reason': reason},
    )

    return JsonResponse({"success": True, "message": msg})


@_gym_staff_required
@require_POST
def freeze_membership_apply(request):
    gym        = getattr(request, 'gym', None)
    enrollment_id = request.POST.get("enrollment_id", "").strip()
    days_raw      = request.POST.get("days", "").strip()
    back_query    = request.POST.get("q", "").strip()
    
    redirect_url = f"/freeze-membership/?tab=freeze&freeze_search={back_query}" if back_query else "/freeze-membership/?tab=freeze"

    try:
        days = int(days_raw)
        if not (1 <= days <= 365):
            raise ValueError
    except (ValueError, TypeError):
        messages.error(request, "Enter a value between 1 and 365.")
        return redirect(redirect_url)

    qs = Enrollment.objects.select_related("user")
    if gym:
        qs = qs.filter(gym=gym)

    try:
        enrollment = qs.get(pk=enrollment_id)
    except Enrollment.DoesNotExist:
        messages.error(request, "Member not found.")
        return redirect(redirect_url)

    if not enrollment.DueDate:
        messages.error(request, f"Member {enrollment.unique_id} has no due date set.")
        return redirect(redirect_url)

    old_due         = enrollment.DueDate
    new_due         = old_due + timedelta(days=days)
    enrollment.DueDate = new_due
    enrollment.save(update_fields=["DueDate"])

    gym_pk = gym.pk if gym else 'none'
    cache.delete(f"enrollment_{enrollment.user_id}_{gym_pk}")
    cache.delete(f"enrollment_status_{enrollment.user_id}_{gym_pk}")

    messages.success(
        request,
        f"{enrollment.fullname} ({enrollment.unique_id}) — extended by {days} day{'s' if days != 1 else ''}: "
        f"{old_due.strftime('%d %b %Y')} → {new_due.strftime('%d %b %Y')}."
    )
    return redirect(redirect_url)

def _format_action_date(dt):
    if not dt:
        return None
    return timezone.localtime(dt).strftime("%d %b %Y %I:%M %p")


def _action_label(user):
    if not user:
        return None
    return user.get_full_name() or user.username


@_gym_staff_required
def transferred_members(request):
    gym = getattr(request, 'gym', None)
    qs = (
        EnrollmentTransfer.objects
        .filter(previous_gym=gym)
        .select_related('new_gym', 'member', 'previous_enrollment', 'action_taken_by')
        .order_by('-created_at')
    )

    summary = {
        "total":    qs.count(),
        "pending":  qs.filter(status='pending').count(),
        "inactive": qs.filter(status='inactive').count(),
        "deleted":  qs.filter(status='deleted').count(),
    }

    rows = [
        {
            "id":                   t.id,
            "member_name":          t.previous_enrollment.fullname if t.previous_enrollment else (t.member.get_full_name() or t.member.username),
            "mobile_number":        t.mobile_number,
            "member_id":            t.previous_member_id,
            "plan_name":            t.previous_plan_name or "—",
            "joining_date":         t.previous_joining_date.strftime("%d %b %Y") if t.previous_joining_date else "—",
            "new_gym_name":         t.new_gym.gym_name,
            "new_gym_joining_date": t.new_gym_joining_date.strftime("%d %b %Y"),
            "previous_due_date":    t.previous_due_date.strftime("%d %b %Y") if t.previous_due_date else "—",
            "pending_amount":       float(t.previous_pending_amount),
            "last_payment_amount":  float(t.last_payment_amount) if t.last_payment_amount else 0,
            "last_payment_date":    t.last_payment_date.strftime("%d %b %Y") if t.last_payment_date else "—",
            "status":               t.status,
            "action_by":            _action_label(t.action_taken_by),
            "action_date":          _format_action_date(t.action_date),
        }
        for t in qs
    ]

    return render(request, "transferred_members.html", {"rows": rows, "summary": summary,})


@_gym_staff_required
@require_POST
def transfer_mark_inactive(request, transfer_id):
    gym      = getattr(request, 'gym', None)
    transfer = get_object_or_404(EnrollmentTransfer, id=transfer_id, previous_gym=gym)

    if transfer.status != 'pending':
        return JsonResponse({"error": "This transfer has already been actioned."}, status=400)

    with transaction.atomic():
        if transfer.previous_enrollment_id:
            Enrollment.objects.filter(id=transfer.previous_enrollment_id).update(is_active=False)
        transfer.status          = 'inactive'
        transfer.action_taken_by = request.user
        transfer.action_date     = timezone.now()
        transfer.save(update_fields=['status', 'action_taken_by', 'action_date'])

    return JsonResponse({
        "ok":          True,
        "status":      transfer.status,
        "action_by":   _action_label(request.user),
        "action_date": _format_action_date(transfer.action_date),
    })


@_gym_staff_required
@require_POST
def transfer_delete_enrollment(request, transfer_id):
    gym      = getattr(request, 'gym', None)
    transfer = get_object_or_404(EnrollmentTransfer, id=transfer_id, previous_gym=gym)

    if transfer.status != 'pending':
        return JsonResponse({"error": "This transfer has already been actioned."}, status=400)

    with transaction.atomic():
        if transfer.previous_enrollment_id:
            Enrollment.objects.filter(id=transfer.previous_enrollment_id).delete()
        transfer.status          = 'deleted'
        transfer.action_taken_by = request.user
        transfer.action_date     = timezone.now()
        transfer.save(update_fields=['status', 'action_taken_by', 'action_date'])

    return JsonResponse({
        "ok":          True,
        "status":      transfer.status,
        "action_by":   _action_label(request.user),
        "action_date": _format_action_date(transfer.action_date),
    })


@_gym_staff_required
def attendance_analytics(request):
    gym = getattr(request, 'gym', None)
    cache_key = f"admin_attendance_data_{gym.pk if gym else 'super'}"
    cached = cache.get(cache_key)

    if cached is None:
        from django.db.models import Count, Max
        from django.db.models.functions import ExtractWeekDay, ExtractHour, TruncMonth
        from collections import defaultdict

        now     = timezone.now()
        today   = timezone.localdate()
        last_30 = now - timedelta(days=30)

        qs        = Attendence_model.objects.all()
        enroll_qs = Enrollment.objects.all()
        if gym:
            qs        = qs.filter(gym=gym)
            enroll_qs = enroll_qs.filter(gym=gym)

        today_count     = qs.filter(date=today).count()
        yesterday_count = qs.filter(date=today - timedelta(days=1)).count()
        today_delta     = today_count - yesterday_count

        ordered_dow = [2, 3, 4, 5, 6, 7, 1]
        day_labels  = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        dow = (
            qs.filter(date__gte=last_30.date())
            .annotate(dow=ExtractWeekDay('date'))
            .values('dow')
            .annotate(total=Count('id'))
            .order_by('dow')
        )
        dow_lookup = {d['dow']: d['total'] for d in dow}
        day_data   = [dow_lookup.get(d, 0) for d in ordered_dow]

        # ── Reusable hour-label helper ──
        def _fmt_hour(h):
            """0-23 -> 12am, 1am..11am, 12pm, 1pm..11pm. Never 0am/24pm."""
            h = h % 24
            if h == 0:
                return "12am"
            if h < 12:
                return f"{h}am"
            if h == 12:
                return "12pm"
            return f"{h - 12}pm"

        # ── Full 24-hour aggregation, NULL-safe ──
        hour_range = list(range(24))
        hourly = (
            qs.filter(date__gte=last_30.date())
            .annotate(hr=ExtractHour("timestamp"))
            .filter(hr__isnull=False)          # exclude NULLs before aggregating
            .values("hr")
            .annotate(total=Count("id"))
            .order_by("hr")
        )
        hour_lookup = {h['hr']: h['total'] for h in hourly if h['hr'] is not None}

        hour_labels = [_fmt_hour(h) for h in hour_range]
        hour_data   = [hour_lookup.get(h, 0) for h in hour_range]

        # ── Peak hour: robust, wraps 23 -> 0, handles empty data ──
        if hour_lookup:
            peak_hr  = max(hour_lookup, key=hour_lookup.get)
            next_hr  = (peak_hr + 1) % 24
            peak_hr_label = f"{_fmt_hour(peak_hr)} – {_fmt_hour(next_hr)}"
        else:
            peak_hr_label = '—'

        busiest_day = day_labels[day_data.index(max(day_data))] if any(day_data) else '—'

        # ── 24-hour heatmap ──
        heatmap_raw = (
            qs.filter(date__gte=last_30.date())
            .annotate(dow=ExtractWeekDay('date'), hr=ExtractHour('timestamp'))
            .filter(hr__isnull=False)
            .values('dow', 'hr')
            .annotate(total=Count('id'))
        )
        hm = defaultdict(lambda: defaultdict(int))
        for row in heatmap_raw:
            hm[row['dow']][row['hr']] = row['total']

        heatmap = {
            label: [hm[db_dow].get(h, 0) for h in hour_range]
            for label, db_dow in zip(day_labels, ordered_dow)
        }

        six_months_ago = now - timedelta(days=180)
        monthly = (
            qs.filter(date__gte=six_months_ago.date())
            .annotate(month=TruncMonth('date'))
            .values('month')
            .annotate(total=Count('id'))
            .order_by('month')
        )
        month_labels = [m['month'].strftime("%b %Y") for m in monthly if m['month']]
        month_data   = [m['total'] for m in monthly]

        all_last_seen   = qs.values('user_id').annotate(last_date=Max('date'))
        absent_rows     = [r for r in all_last_seen if (today - r['last_date']).days >= 5]
        absent_user_ids = [r['user_id'] for r in absent_rows]
        enrollment_map  = {
            e.user_id: e
            for e in enroll_qs.filter(user_id__in=absent_user_ids)
        }

        at_risk = []
        for row in absent_rows:
            enroll = enrollment_map.get(row['user_id'])
            if not enroll:
                continue
            days_absent = (today - row['last_date']).days
            status = (
                'danger'  if days_absent >= 14 else
                'warning' if days_absent >= 7  else
                'notice'
            )
            at_risk.append({
                'name':   enroll.fullname,
                'uid':    enroll.unique_id,
                'last':   row['last_date'].strftime("%b %d"),
                'days':   days_absent,
                'status': status,
            })

        at_risk.sort(key=lambda x: -x['days'])
        at_risk = at_risk[:10]

        total_enrolled    = enroll_qs.count()
        active_this_month = (
            qs.filter(date__year=today.year, date__month=today.month)
            .values('user').distinct().count()
        )
        retention_pct = (
            round(active_this_month / total_enrolled * 100, 1)
            if total_enrolled else 0
        )

        cached = {
            "today_count":       today_count,
            "today_delta":       today_delta,
            "peak_hr_label":     peak_hr_label,
            "busiest_day":       busiest_day,
            "at_risk_count":     len([m for m in at_risk if m['status'] == 'danger']),
            "day_labels":        day_labels,
            "day_data":          day_data,
            "hour_labels":       hour_labels,
            "hour_data":         hour_data,
            "month_labels":      month_labels,
            "month_data":        month_data,
            "heatmap":           heatmap,
            "at_risk":           at_risk,
            "total_enrolled":    total_enrolled,
            "active_this_month": active_this_month,
            "retention_pct":     retention_pct,
        }
        cache.set(cache_key, cached, timeout=120)

    return render(request, "attendance_analysis.html", {
        "gym":               gym,
        "today_count":       cached["today_count"],
        "today_delta":       cached["today_delta"],
        "peak_hr_label":     cached["peak_hr_label"],
        "busiest_day":       cached["busiest_day"],
        "at_risk_count":     cached["at_risk_count"],
        "at_risk":           cached["at_risk"],
        "total_enrolled":    cached["total_enrolled"],
        "active_this_month": cached["active_this_month"],
        "retention_pct":     cached["retention_pct"],
        "day_labels":        json.dumps(cached["day_labels"]),
        "day_data":          json.dumps(cached["day_data"]),
        "hour_labels":       json.dumps(cached["hour_labels"]),
        "hour_data":         json.dumps(cached["hour_data"]),
        "month_labels":      json.dumps(cached["month_labels"]),
        "month_data":        json.dumps(cached["month_data"]),
        "heatmap_json":      json.dumps(cached["heatmap"]),
    })
 
def _invalidate_attendance_cache(gym_id):
    cache.delete(f"admin_attendance_data_{gym_id}")
    cache.delete("admin_attendance_data_super")

@_gym_staff_required
def revenue_view(request):
    from billing.services import revenue_service

    gym = getattr(request, 'gym', None)
    gst_enabled = revenue_service.is_gst_enabled(gym)
    cache_key = f"admin_revenue_{gym.pk if gym else 'super'}"
    data = cache.get(cache_key)

    if data is None:
        today = timezone.localdate()
        last_7_days = today - timedelta(days=7)

        daily_rev = revenue_service.get_daily_series(gym, days=7, metric='collection')
        monthly_rev = revenue_service.get_monthly_series(gym, months=12, metric='collection')
        plan_breakdown = revenue_service.get_plan_revenue_breakdown(gym)

        today_figures = revenue_service.get_today_figures(gym)
        week_figures = revenue_service.get_week_figures(gym, today)
        month_figures = revenue_service.get_month_figures(gym, today)
        lifetime_figures = revenue_service.get_lifetime_figures(gym)
        gst_enabled_flag = revenue_service.is_gst_enabled(gym)
        gym_start_date = gym.created_at.date() if gym and gym.created_at else today
        trend_metric = 'revenue' if gst_enabled_flag else 'collection'
        lifetime_series = revenue_service.get_monthly_series_since(gym, gym_start_date, metric=trend_metric)
        lifetime_trend_labels = [
            f"{today.replace(year=r['year'], month=r['month'], day=1):%b %Y}" for r in lifetime_series
        ]
        lifetime_trend_data = [float(r['value']) for r in lifetime_series]
        lifetime_total_to_date = float(lifetime_figures['revenue'])
        lifetime_as_of_date = today.strftime('%d %b %Y')

        if len(lifetime_trend_data) >= 2 and lifetime_trend_data[-2] > 0:
            lifetime_change_pct = round(
                ((lifetime_trend_data[-1] - lifetime_trend_data[-2]) / lifetime_trend_data[-2]) * 100, 1
            )
            lifetime_trend_up = lifetime_trend_data[-1] >= lifetime_trend_data[-2]
        else:
            lifetime_change_pct = 0.0
            lifetime_trend_up = True

        enroll_qs = Enrollment.objects.filter(is_deleted=False)
        if gym:
            enroll_qs = enroll_qs.filter(gym=gym)

        members = (
            enroll_qs.annotate(month=TruncMonth('doj'))
            .values('month').annotate(count=Count('id')).order_by('month')
        )
        payments_breakdown = (
            enroll_qs.exclude(paymentStatus__isnull=True)
            .values('paymentStatus').annotate(count=Count('id'))
        )
        pending_qs = enroll_qs.filter(pendingAmount__gt=0, paymentStatus="Pending")

        data = {
            "monthly_labels": [f"{today.replace(year=r['year'], month=r['month'], day=1):%b %Y}" for r in monthly_rev],
            "monthly_data":   [float(r['value']) for r in monthly_rev],
            "daily_labels":   [r['date'].strftime("%d %b") for r in daily_rev],
            "daily_data":     [float(r['value']) for r in daily_rev],
            "member_labels":  [x['month'].strftime("%b %Y") for x in members if x['month']],
            "member_data":    [x['count'] for x in members],
            "payment_labels": [x['paymentStatus'] for x in payments_breakdown],
            "payment_data":   [x['count'] for x in payments_breakdown],
            "plan_labels":    [p['plan_name'] for p in plan_breakdown],
            "plan_revenue":   [float(p['revenue']) for p in plan_breakdown],
            "plan_count":     [p['count'] for p in plan_breakdown],

            "lifetime_trend_labels": lifetime_trend_labels,     
            "lifetime_trend_data":   lifetime_trend_data,       
            "lifetime_change_pct":   lifetime_change_pct,       
            "lifetime_trend_up":     lifetime_trend_up,         
            "lifetime_total_to_date": lifetime_total_to_date,   
            "lifetime_as_of_date":    lifetime_as_of_date,      

            "total_revenue":      float(lifetime_figures['revenue']),
            "total_collection":   float(lifetime_figures['collection']),
            "month_revenue":      float(month_figures['revenue']),
            "month_collection":   float(month_figures['collection']),
            "week_revenue":       float(week_figures['revenue']),
            "week_collection":    float(week_figures['collection']),
            "today_revenue":      float(today_figures['revenue']),
            "today_collection":   float(today_figures['collection']),

            "total_members":  enroll_qs.count(),
            "pending_count":  pending_qs.count(),
            "pending_amount": float(pending_qs.aggregate(total=Sum('pendingAmount'))['total'] or 0),
        }
        cache.set(cache_key, data, timeout=60)

    return render(request, "revenue.html", {
        "gym": gym,
        "gst_enabled": gst_enabled,
        "hidden_cards": [],
        **{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in data.items()},
    })

def feature_comp(request):
    return render(request, "whychose.html")

def Refundpolicy(request):
    return render(request, "refundpolicy.html")

def termcondition(request):
    return render(request, "termcondition.html")

def privacypolicy(request):
    return render(request, "privacypolicy.html")

def guide(request):
    return render(request, "guide.html")

def aiattendance(request):
    return render(request, "aiattendance.html")
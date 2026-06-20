# AuthFit/views.py

import secrets
import os
import json
import functools
from datetime import date, timedelta

from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_log, logout
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.paginator import Paginator
from django.db import transaction
import cloudinary.uploader
from cloudinary.utils import cloudinary_url
from PIL import Image
import io
import logging
from urllib.parse import urlencode
from Gym.models import Gym                          
from AuthFit.models import (
    Contact, Enrollment, MembershipPlan, Trainer,
    Attendence as Attendence_model, GymNotification
)
from AuthFit.rate_limit import check_login_attempt, reset_attempt, record_failed_attempt ,get_client_ip
from .attendance import mark_attendance
from .forms import UserLogin
from urllib.parse import quote
from Shop.notifications import notify_staff_new_enrollment
from django.contrib.auth.hashers import check_password
logger = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
ALLOWED_EXTENSIONS  = {'.jpg', '.jpeg', '.png', '.webp'}
INTERNAL_API_KEY    = os.environ.get("INTERNAL_API_KEY", "")

def test_gym(request):
    return JsonResponse({
        "gym": request.gym.gym_code if request.gym else None,
        "role": request.staff_role,
        "user": request.user.username if request.user.is_authenticated else None,
    })
# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _check_internal_key(request):
    provided = request.headers.get("X-Internal-Key", "")
    if not INTERNAL_API_KEY or not provided:
        return False
    return secrets.compare_digest(provided, INTERNAL_API_KEY)


def is_staff(user):
    return user.is_staff or user.is_superuser


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


def _get_gym(request):
    if request.user.is_superuser:
        return None
    return getattr(request, 'gym', None)


def _gym_staff_required(view_fn):
    @login_required
    @functools.wraps(view_fn)
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_staff or request.user.is_superuser):
            return HttpResponseForbidden("Staff access required.")
        return view_fn(request, *args, **kwargs)
    return wrapped


# ──────────────────────────────────────────────────────────────────────────────
# Internal API views (called by face recognition service / cron jobs)
# ──────────────────────────────────────────────────────────────────────────────

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

        face_embeddings = enrollment.face_embeddings or []
        MAX_EMB = 7
        for emb in embeddings:
            if len(face_embeddings) >= MAX_EMB:
                face_embeddings.pop(0)
            face_embeddings.append(emb)

        enrollment.face_embeddings = face_embeddings
        enrollment.face_enrolled   = True
        enrollment.save(update_fields=["face_embeddings", "face_enrolled"])
        cache.delete(f"face_users_{enrollment.gym_id}")

        logger.info("Embeddings updated for enrollment_id=%s", enrollment.id)
        return JsonResponse({"status": "success", "total_embeddings": len(face_embeddings)})

    except Enrollment.DoesNotExist:
        return JsonResponse({"error": "Enrollment not found"}, status=404)
    except Exception:
        logger.exception("Error in save_embeddings_batch")
        return JsonResponse({"error": "Internal error"}, status=500)


@csrf_exempt
def mark_attendance_api(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        if not _check_internal_key(request):
            return JsonResponse({"error": "Unauthorized"}, status=403)

        data      = json.loads(request.body)
        unique_id = data.get("unique_id")
        gym_id    = data.get("gym_id")

        if not unique_id:
            return JsonResponse({"error": "Missing unique_id"}, status=400)

        result = mark_attendance(unique_id, gym_id=gym_id)
        return JsonResponse(result)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Error in mark_attendance_api")
        return JsonResponse({"error": "Internal error"}, status=500)


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
        enrollments = Enrollment.objects.filter(
            gym_id=gym_id,
            face_enrolled=True,
        ).exclude(face_embeddings=[])

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


# ──────────────────────────────────────────────────────────────────────────────
# Auth views
# ──────────────────────────────────────────────────────────────────────────────

def signupPage(request):
    if request.user.is_authenticated:
        return redirect('/')

    gym = getattr(request, 'gym', None)

    if request.method == "POST":
        form = UserLogin(request.POST, gym=gym)
        if form.is_valid():
            user = form.save()
            auth_log(request, user)
            messages.success(request, "Account created successfully!")
            return redirect('/')
    else:
        form = UserLogin(gym=gym)

    return render(request, 'registration/signup.html', {'form': form})


def loginPage(request):
    if request.user.is_authenticated:
        return redirect('/')

    next_url = request.GET.get('next') or request.POST.get('next', '/')

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
            return redirect(_safe_next(next_url, request))
        else:
            check_password(password, "pbkdf2_sha256$600000$dummy$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")
            record_failed_attempt(ip, phone)
            messages.error(request, "Incorrect phone number or password.")
            return redirect(f'/login/?{urlencode({"next": next_url})}')

    return render(request, 'registration/login.html', {'next': next_url})


def handlelogout(request):
    logout(request)
    messages.success(request, "Logged out successfully.")
    return redirect('/')


# ──────────────────────────────────────────────────────────────────────────────
# Public / member views
# ──────────────────────────────────────────────────────────────────────────────

def homePage(request):
    gym = getattr(request, 'gym', None)

    # ── SaaS root domain — no gym context ─────────────────────────────────
    if gym is None:
        if request.user.is_superuser:
            gyms = Gym.objects.all().order_by('gym_name')
            return render(request, 'saas_home.html', {'gyms': gyms})
        return render(request, 'saas_home.html')

    # ── Gym domain — load gym-specific content ─────────────────────────────
    notif_key         = f"notifications_{gym.pk}"
    gym_notifications = cache.get(notif_key)
    if gym_notifications is None:
        gym_notifications = list(
            GymNotification.objects
            .filter(gym=gym, is_active=True)
            .values("icon", "message")
        )
        cache.set(notif_key, gym_notifications, timeout=3600)

    plans_key = f"membership_plans_{gym.pk}"
    plans     = cache.get(plans_key)
    if plans is None:
        plans = list(
            MembershipPlan.objects
            .filter(gym=gym)
            .values("id", "plan", "price", "duration_days")
        )
        cache.set(plans_key, plans, timeout=3600)

    enrolled    = False
    isStaff     = False
    isSuperuser = False

    if request.user.is_authenticated:
        isStaff     = request.user.is_staff
        isSuperuser = request.user.is_superuser

        cache_key = f"enrolled_{request.user.id}_{gym.pk}"
        enrolled  = cache.get(cache_key)
        if enrolled is None:
            enrolled = Enrollment.objects.filter(
                user=request.user, gym=gym
            ).exists()
            cache.set(cache_key, enrolled, timeout=300)

    return render(request, 'gym_home.html', {
        "gym":               gym,
        "enrolled":          enrolled,
        "isStaff":           isStaff,
        "isSuperuser":       isSuperuser,
        "gym_notifications": gym_notifications,
        "plans":             plans,
    })


def stats_api(request):
    gym = getattr(request, 'gym', None)
    qs  = Enrollment.objects.all()
    if gym:
        qs = qs.filter(gym=gym)
    return JsonResponse({"total_users": qs.count()})


def contact(request):
    gym = getattr(request, 'gym', None)

    # FIX: Contact.gym is non-nullable — block on bare domain (gym=None)
    if not gym:
        messages.error(request, "Contact form is unavailable on this domain.")
        return redirect('/')

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

    return render(request, 'contact.html')


def workout(request):
    return render(request, 'workout.html')


def download_app(request):
    """
    Renders the app download page.
    The `gym` object is injected by your context processor automatically,
    but we also pass gym_name / gym_short explicitly so the template
    has clean variables to use everywhere (title, meta, aria-label, etc.)
    """
    gym = getattr(request, 'gym', None)  # set by your context processor
 
    gym_name  = gym.gym_name if gym else "EnterGYM"
    # A short slug for things like the APK filename or meta title
    gym_short = gym_name.replace(" ", "")  # "GoldenGYM", "EnterGYM", etc.
 
    return render(request, 'download.html', {
        'gym_name':  gym_name,
        'gym_short': gym_short,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Member views
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def enrollment(request):
    gym = getattr(request, 'gym', None)
    
    # FIX: scope check to this gym — without gym filter a user enrolled at
    # fitzone gets redirected away from ironhouse enrollment page
    if Enrollment.objects.filter(user=request.user, gym=gym).exists():
        return redirect('/profile/')

    plans    = MembershipPlan.objects.filter(gym=gym) if gym else MembershipPlan.objects.none()
    trainers = Trainer.objects.filter(gym=gym) if gym else Trainer.objects.none()

    if request.method == "POST":
        name       = request.POST.get('name', '').strip()
        email      = request.POST.get('email', '').strip()
        phone      = request.POST.get('phone', '').strip()
        gender     = request.POST.get('gender')
        plan_id    = request.POST.get('plan')
        trainer_id = request.POST.get('trainer')
        reference  = request.POST.get('reference', '').strip()
        address    = request.POST.get('address', '').strip()

        selected_trainer = None
        if trainer_id:
            selected_trainer = Trainer.objects.filter(id=trainer_id, gym=gym).first()
            if not selected_trainer:
                messages.error(request, "Selected trainer does not exist.")
                return redirect('/enrollment/')

        selected_plan = MembershipPlan.objects.filter(id=plan_id, gym=gym).first()
        if not selected_plan:
            messages.error(request, "Selected plan does not exist.")
            return redirect('/enrollment/')

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
        )
        enroll.save()

        transaction.on_commit(lambda: notify_staff_new_enrollment(enroll))

        gym_pk = gym.pk if gym else 'none'
        cache.delete(f"enrollment_{request.user.id}_{gym_pk}")
        cache.delete(f"profile_image_{request.user.id}")
        cache.delete(f"enrolled_{request.user.id}_{gym_pk}")
        cache.delete(f"enrollment_status_{request.user.id}_{gym_pk}")

        messages.success(request, "Welcome aboard! Your gym membership has been activated.")
        return redirect('/profile/')

    return render(request, 'enrollment.html', {"plans": plans, "trainers": trainers})


@login_required
def Profile(request):
    gym = getattr(request, 'gym', None)

    enrollment = (
        Enrollment.objects
        .filter(user=request.user, gym=gym)
        .select_related("selectPlan", "trainer")
        .first()
    )

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

    return render(request, "profile.html", {
        "enrollment":     enrollment,
        "image_url":      image_url,
        "is_expired":     enrollment.is_expired if enrollment else False,
        "days_remaining": enrollment.days_remaining if enrollment else 0,
        "plans":          plans,
    })


@login_required
def upload_profile_pic(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    gym        = getattr(request, 'gym', None)
    enrollment = Enrollment.objects.filter(user=request.user, gym=gym).first()
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


@login_required
def attendance_page(request):
    gym        = getattr(request, 'gym', None)
    enrollment = Enrollment.objects.filter(user=request.user, gym=gym).first()
    if not enrollment:
        return redirect('/enrollment/')

    today = timezone.localdate()
    user  = request.user

    # FIX: both queries scoped to gym
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
    })


@login_required
@require_POST
def renew_membership(request):
    gym        = getattr(request, 'gym', None)
    gym_pk     = gym.pk if gym else 'none'
    enrollment = get_object_or_404(Enrollment, user=request.user, gym=gym)

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


# ──────────────────────────────────────────────────────────────────────────────
# Staff views — all scoped to request.gym
# ──────────────────────────────────────────────────────────────────────────────

@_gym_staff_required
def whatsapp_pending_users(request):
    gym = getattr(request, 'gym', None)

    qs = Enrollment.objects.filter(paymentStatus="Pending").select_related("selectPlan", "gym")
    if gym:
        qs = qs.filter(gym=gym)

    pending_with_links = []
    for e in qs:
        # FIX: use gym name from enrollment, not hardcoded "EnterGYM"
        gym_name = e.gym.gym_name if e.gym else "EnterGYM"
        msg = (
            f"Hello {e.fullname}! Reminder from {gym_name}: "
            f"your payment of Rs.{e.pendingAmount} is pending. "
            f"Please clear your dues at your earliest convenience. Thank you!"
        )
        pending_with_links.append({
            "enrollment": e,
            "wa_link":    f"https://wa.me/91{e.phone}?text={quote(msg)}",
        })

    return render(request, "admin_whatsapp.html", {"pending": pending_with_links})


@_gym_staff_required
def payment_management(request):
    gym           = getattr(request, 'gym', None)
    status_filter = request.GET.get("filter", "pending")
    since         = timezone.now() - timedelta(days=7)
    METHOD_LABELS = {"C": "Cash", "U": "UPI", "B": "UPI + Cash"}

    qs = Enrollment.objects.select_related("selectPlan", "trainer")
    if gym:
        qs = qs.filter(gym=gym)

    if status_filter == "done":
        qs = qs.filter(created_at__gte=since, paymentStatus="Done").order_by("-created_at")
    else:
        qs = qs.filter(paymentStatus="Pending").order_by("-created_at")

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
        for e in qs
    ]

    base_qs       = Enrollment.objects.filter(gym=gym) if gym else Enrollment.objects.all()
    pending_count = base_qs.filter(paymentStatus="Pending").count()
    paid_count    = base_qs.filter(created_at__gte=since, paymentStatus="Done").count()

    return render(request, "payment_management.html", {
        "rows":                 rows,
        "status_filter":        status_filter,
        "total_pending_amount": sum(r["pending"] for r in rows),
        "total_count":          len(rows),
        "pending_count":        pending_count,
        "paid_count":           paid_count,
    })


@login_required
@user_passes_test(is_staff)
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

        uid = enrollment.user_id
        gp  = gym.pk if gym else 'none'
        cache.delete(f"admin_revenue_{gp}")
        cache.delete(f"enrollment_{uid}_{gp}")
        cache.delete(f"enrollment_status_{uid}_{gp}")

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


@_gym_staff_required
def today_attendance(request):
    gym   = getattr(request, 'gym', None)
    today = timezone.localdate()

    cache_key = f"today_attendance_{gym.pk if gym else 'super'}_{today}"
    cached    = cache.get(cache_key)
    if cached:
        return render(request, "today_attendance.html", cached)

    # FIX: use prefetch_related instead of select_related for reverse FK
    # User → Enrollment is a FK (one user can have many enrollments across gyms)
    # select_related("user__enrollment__...") silently fails for FK reverse paths
    qs = (
        Attendence_model.objects
        .filter(date=today)
        .select_related("user")
        .prefetch_related("user__enrollment_set__selectPlan",
                          "user__enrollment_set__trainer")
        .order_by("timestamp")
    )
    if gym:
        qs = qs.filter(gym=gym)

    morning, evening = [], []

    for rec in qs:
        # FIX: scope enrollment lookup to this gym
        enrollment = rec.user.enrollment_set.filter(gym=gym).first()
        image_url  = None

        if enrollment and enrollment.face_image:
            try:
                public_id = (
                    enrollment.face_image.public_id
                    if hasattr(enrollment.face_image, "public_id")
                    else str(enrollment.face_image)
                )
                if public_id:
                    image_url, _ = cloudinary_url(
                        public_id,
                        width=60, height=60,
                        crop="fill", gravity="face",
                        fetch_format="auto", quality="auto",
                        secure=True,
                    )
            except Exception:
                logger.exception("Cloudinary URL error for user %s", rec.user.id)

        entry = {
            "id":             rec.id,
            "time":           rec.timestamp.strftime("%I:%M %p"),
            "name":           enrollment.fullname if enrollment else rec.user.username,
            "unique_id":      enrollment.unique_id if enrollment else "—",
            "image_url":      image_url,
            "pending_amount": float(enrollment.pendingAmount) if enrollment else 0,
            "due_date":       enrollment.DueDate.strftime("%d %b %Y") if enrollment and enrollment.DueDate else "—",
            "is_expired":     enrollment.is_expired if enrollment else False,
            "phone":          enrollment.phone if enrollment else "—",
            "address":        enrollment.address if enrollment else "—",
            "plan":           enrollment.selectPlan.plan if enrollment and enrollment.selectPlan else "—",
            "plan_price":     float(enrollment.selectPlan.price) if enrollment and enrollment.selectPlan else 0,
            "trainer":        enrollment.trainer.name if enrollment and enrollment.trainer else "No Trainer",
            "gender":         enrollment.get_gender_display() if enrollment else "—",
            "doj":            enrollment.doj.strftime("%d %b %Y") if enrollment and enrollment.doj else "—",
            "payment_status": enrollment.paymentStatus if enrollment else "—",
            "days_remaining": enrollment.days_remaining if enrollment else 0,
            "payment_date":   enrollment.paymentDate.strftime("%d %b %Y") if enrollment and enrollment.paymentDate else "—",
        }
        (morning if rec.timestamp.hour < 14 else evening).append(entry)

    context = {
        "sections": [("Morning", "🌅", morning), ("Evening", "🌆", evening)],
        "today":    today,
        "total":    len(morning) + len(evening),
    }
    cache.set(cache_key, context, timeout=120)
    return render(request, "today_attendance.html", context)


@_gym_staff_required
def freeze_membership(request):
    gym   = getattr(request, 'gym', None)
    query = request.GET.get("q", "").strip()

    qs = Enrollment.objects.select_related("selectPlan").order_by("fullname")
    if gym:
        qs = qs.filter(gym=gym)
    if query:
        qs = qs.filter(unique_id=query)

    paginator = Paginator(qs, 20)
    page_obj  = paginator.get_page(request.GET.get("page", 1))

    return render(request, "freeze_membership.html", {"page_obj": page_obj, "query": query})


@_gym_staff_required
@require_POST
def freeze_membership_apply(request):
    gym        = getattr(request, 'gym', None)
    enrollment_id = request.POST.get("enrollment_id", "").strip()
    days_raw      = request.POST.get("days", "").strip()
    back_query    = request.POST.get("q", "").strip()

    redirect_url = f"/freeze-membership/?q={back_query}" if back_query else "/freeze-membership/"

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
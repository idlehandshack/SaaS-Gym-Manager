# AuthFit/views.py

import secrets
import os
import json
from django.db.models import Q
import functools
from datetime import date, timedelta
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
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
from django.db import transaction ,IntegrityError
import cloudinary.uploader
from cloudinary.utils import cloudinary_url
from PIL import Image
from django.db.models import Count, Max
import io
import logging
from urllib.parse import urlencode
from Gym.models import Gym                          
from AuthFit.models import (
    Contact, Enrollment, EnrollmentTransfer, MembershipPlan, Trainer,
    Attendence as Attendence_model, GymNotification
)
from django.db.models import Sum, Count
from django.db.models.functions import ExtractWeekDay, ExtractHour, TruncMonth,TruncDay 
from collections import defaultdict
from django.views.decorators.cache import cache_page
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
        # OLD ❌
        # if not (request.user.is_staff or request.user.is_superuser):

        # NEW ✅
        if not getattr(request, 'is_gym_staff', False):
            return HttpResponseForbidden("Staff access required.")
        return view_fn(request, *args, **kwargs)
    return wrapped


def _gym_role_required(*allowed_roles):
    """
    Stricter than _gym_staff_required: restricts to specific staff_role values
    within the current gym. Super admins always pass.
    """
    def decorator(view_fn):
        @_gym_staff_required
        @functools.wraps(view_fn)
        def wrapped(request, *args, **kwargs):
            if not (request.is_super_admin or request.staff_role in allowed_roles):
                return HttpResponseForbidden("You don't have permission for this action.")
            return view_fn(request, *args, **kwargs)
        return wrapped
    return decorator

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

@_gym_staff_required
def contact_inquiries(request):
    gym = getattr(request, 'gym', None)
    if gym is None:
        return HttpResponseForbidden("No gym context available.")

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
            return redirect(_safe_next(next_url, request))
        else:
            check_password(password, "pbkdf2_sha256$600000$dummy$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")
            record_failed_attempt(ip, phone)
            messages.error(request, "Incorrect phone number or password.")
            return redirect(f'/login/?{urlencode({"next": next_url})}')

    login_template = (
        "registration/saas_login.html" if gym is None
        else "registration/login.html"
    )

    return render(request, login_template, {'next': next_url, 'gym': gym})


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
        isStaff     = getattr(request, 'is_gym_staff', False)
        isSuperuser = getattr(request, 'is_super_admin', False)

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

    return render(request, 'contact.html',{
        "gym":gym
    })


def workout(request):
    gym = getattr(request, 'gym', None)
    return render(request, 'workout.html',{
        "gym":gym
    })


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


@_gym_role_required('gym_owner', 'receptionist')
def membership_plans(request):
    gym = getattr(request, 'gym', None)
    if gym is None:
        return HttpResponseForbidden("No gym context available.")

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

# ──────────────────────────────────────────────────────────────────────────────
# Member views
# ──────────────────────────────────────────────────────────────────────────────

@login_required
def enrollment(request):
    gym = getattr(request, 'gym', None)

    if Enrollment.objects.filter(user=request.user, gym=gym).exists():
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

        # ── Cross-gym transfer check ────────────────────────────────────
        # Only an ACTIVE enrollment at another gym triggers the popup. Once
        # the old gym actions it (inactive/deleted), it stops blocking.
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

        # ── Re-validate the referenced old enrollment on confirm ───────
        # Never trust client-sent financial figures — only the id, then
        # re-fetch fresh data server-side.
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
            # If it no longer matches (e.g. old gym already actioned it),
            # don't block the member — just proceed as a normal enrollment.

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
                # A pending transfer already exists for this source enrollment
                # (e.g. duplicate/double-click submission) — the existing
                # pending record already covers it, so skip silently.
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
        "gym":       gym,
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
        "gym" : gym,
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

def _panel_data(e, kind, **extra):
    """
    Build a flat, JSON-serialisable dict for the slide-out detail panel.
    `kind` tells the frontend which fields to show ('due' | 'expiring' | 'expired').
    Dates are converted to strings — date objects are not JSON-serialisable.
    """
    data = {
        "kind": kind,
        "unique_id": e.unique_id,
        "name": e.fullname,
        "phone": e.phone,
        "email": e.email,
        "gender": e.get_gender_display() if e.gender else None,
        "address": e.address,
        "plan": e.selectPlan.plan if e.selectPlan else None,
        "plan_price": float(e.selectPlan.price) if e.selectPlan else None,
        "trainer": e.trainer.name if e.trainer else None,
        "payment_status": e.paymentStatus,
        "pending_amount": float(e.pendingAmount),
        "due_date": e.DueDate.strftime("%d %b %Y") if e.DueDate else None,
        "doj": e.doj.strftime("%d %b %Y") if e.doj else None,
        "is_expired": e.is_expired,
        "days_remaining": e.days_remaining,
    }
    data.update(extra)
    return data
 
 
@_gym_staff_required
def whatsapp_pending_users(request):
    gym = getattr(request, 'gym', None)
    today = timezone.now().date()
 
    base_qs = Enrollment.objects.select_related("selectPlan", "gym", "trainer")
    if gym:
        base_qs = base_qs.filter(gym=gym)
 
    # ── PANEL 1: Due Payments (unchanged logic) ──
    pending_qs = base_qs.filter(paymentStatus="Pending").order_by("DueDate")
 
    pending_with_links = []
    for e in pending_qs:
        gym_name = e.gym.gym_name if e.gym else "EnterGYM"
        msg = (
            f"Hello {e.fullname}! Reminder from {gym_name}: "
            f"your payment of Rs.{e.pendingAmount} is pending. "
            f"Please clear your dues at your earliest convenience. Thank you!"
        )
        pending_with_links.append({
            "enrollment": e,
            "wa_link":    f"https://wa.me/91{e.phone}?text={quote(msg)}",
            "panel_data": _panel_data(e, "due"),
        })
 
    # ── PANEL 2: Expiring Soon (DueDate within next 2 days, not yet expired) ──
    expiring_cutoff = today + timedelta(days=2)
    expiring_qs = base_qs.filter(
        DueDate__gte=today,
        DueDate__lte=expiring_cutoff,
    ).order_by("DueDate")
 
    expiring_with_links = []
    for e in expiring_qs:
        gym_name = e.gym.gym_name if e.gym else "EnterGYM"
        msg = (
            f"Hello {e.fullname}! Reminder from {gym_name}: "
            f"your membership is expiring on {e.DueDate.strftime('%d %b %Y')}. "
            f"Please renew soon to avoid interruption. Thank you!"
        )
        expiring_with_links.append({
            "enrollment": e,
            "wa_link":    f"https://wa.me/91{e.phone}?text={quote(msg)}",
            "panel_data": _panel_data(e, "expiring"),
        })
 
    # ── PANEL 3: Expired Members (DueDate already passed) ──
    expired_qs = base_qs.filter(DueDate__lt=today).order_by("-DueDate")
 
    expired_with_links = []
    for e in expired_qs:
        gym_name = e.gym.gym_name if e.gym else "EnterGYM"
        expired_days = (today - e.DueDate).days
        msg = (
            f"Hello {e.fullname}! Your membership at {gym_name} expired on "
            f"{e.DueDate.strftime('%d %b %Y')}. Please renew to continue access. Thank you!"
        )
        expired_with_links.append({
            "enrollment": e,
            "wa_link":    f"https://wa.me/91{e.phone}?text={quote(msg)}",
            "expired_days": expired_days,
            "panel_data": _panel_data(e, "expired", expired_days=expired_days),
        })
 
    return render(request, "admin_whatsapp.html", {
        "pending": pending_with_links,
        "expiring_soon": expiring_with_links,
        "expired_members": expired_with_links,
        "pending_count": len(pending_with_links),
        "expiring_count": len(expiring_with_links),
        "expired_count": len(expired_with_links),
        "gym": gym,
    })
 


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
        "gym": gym,
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
        "gym" : gym
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


# ──────────────────────────────────────────────────────────────────────────────
# Transferred Members — old gym's view into outgoing transfers
# ──────────────────────────────────────────────────────────────────────────────

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

    return render(request, "transferred_members.html", {"rows": rows, "summary": summary})


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
 
        now    = timezone.now()
        today  = timezone.localdate()
        last_30 = now - timedelta(days=30)
 
        qs        = Attendence_model.objects.all()
        enroll_qs = Enrollment.objects.all()
        if gym:
            qs        = qs.filter(gym=gym)
            enroll_qs = enroll_qs.filter(gym=gym)
 
        # ── Today vs yesterday ────────────────────────────────────────────
        today_count     = qs.filter(date=today).count()
        yesterday_count = qs.filter(date=today - timedelta(days=1)).count()
        today_delta     = today_count - yesterday_count
 
        # ── Day-of-week traffic ───────────────────────────────────────────
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
 
        # ── Hourly traffic ────────────────────────────────────────────────
        hourly = (
            qs.filter(date__gte=last_30.date())
            .annotate(hr=ExtractHour('timestamp'))
            .values('hr')
            .annotate(total=Count('id'))
            .order_by('hr')
        )
        hour_lookup = {h['hr']: h['total'] for h in hourly}
        hour_range  = list(range(5, 12)) + list(range(16, 23))
 
        def _fmt(h):
            hh  = h if h <= 12 else h - 12
            suf = 'am' if h < 12 else 'pm'
            return f"{hh}{suf}" if h != 12 else '12p'
 
        hour_labels   = [_fmt(h) for h in hour_range]
        hour_data     = [hour_lookup.get(h, 0) for h in hour_range]
 
        if hour_lookup:
            peak_hr       = max(hour_lookup, key=hour_lookup.get)
            next_hr       = peak_hr + 1
            peak_hr_label = (
                f"{peak_hr if peak_hr <= 12 else peak_hr - 12}"
                f"{'am' if peak_hr < 12 else 'pm'}"
                f" – "
                f"{next_hr if next_hr <= 12 else next_hr - 12}"
                f"{'am' if next_hr < 12 else 'pm'}"
            )
        else:
            peak_hr_label = '—'
 
        busiest_day = day_labels[day_data.index(max(day_data))] if any(day_data) else '—'
 
        # ── Heatmap ───────────────────────────────────────────────────────
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
 
        # ── Monthly trend ─────────────────────────────────────────────────
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
 
        # ── At-risk members (no N+1) ──────────────────────────────────────
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
 
        # ── Retention ─────────────────────────────────────────────────────
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
 

@_gym_staff_required
def revenue_view(request):
    gym = getattr(request, 'gym', None)

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

    return render(request, "revenue.html", {
        "gym": gym,
        "monthly_labels": json.dumps(data["monthly_labels"]),
        "monthly_data":   json.dumps(data["monthly_data"]),
        "daily_labels":   json.dumps(data["daily_labels"]),
        "daily_data":     json.dumps(data["daily_data"]),
        "member_labels":  json.dumps(data["member_labels"]),
        "member_data":    json.dumps(data["member_data"]),
        "payment_labels": json.dumps(data["payment_labels"]),
        "payment_data":   json.dumps(data["payment_data"]),
        "plan_labels":    json.dumps(data["plan_labels"]),
        "plan_revenue":   json.dumps(data["plan_revenue"]),
        "plan_count":     json.dumps(data["plan_count"]),
        "total_revenue":  data["total_revenue"],
        "today_revenue":  data["today_revenue"],
        "total_members":  data["total_members"],
        "pending_count":  data["pending_count"],
        "pending_amount": data["pending_amount"],
    })
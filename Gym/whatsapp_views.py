# Gym/whatsapp_views.py
"""
WhatsApp Cloud API dashboard views — setup, connect, disconnect, verify,
send-test-message. Follows the exact conventions already in Gym/views.py
(upi_payment_settings, gst_profile_edit): @permission_required decorator,
GET renders form / POST validates+saves, messages.success/error, redirect
to the same named URL. Views contain NO business logic beyond form
handling — all WhatsApp API interaction happens in
Gym.services.whatsapp_service, per the spec's "views must not contain
business logic" rule.
"""

import functools
import re
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from AuthFit.permissions import permission_required
from .forms import WhatsAppSettingsForm
from .models import GymWhatsAppSettings
from .services import whatsapp_service
from .services.whatsapp_service import (
    WhatsAppError, WhatsAppNotConfigured, WhatsAppDisabled,
    WhatsAppInvalidPhoneNumber, WhatsAppRateLimitExceeded,
)
def _mask_phone(phone: str) -> str:
    """
    +919876543210 -> +91******3210
    Keeps the country code (everything up to and including the first
    non-leading-plus digit group is approximated as the first 3 chars
    after '+') and the last 4 digits visible; masks everything between.
    Falls back to returning the input unchanged if it's too short to
    mask meaningfully (avoids over-masking short/malformed values into
    something unreadable).
    """
    if not phone or len(phone) < 8:
        return phone
    prefix = phone[:3]        # e.g. "+91"
    suffix = phone[-4:]       # last 4 digits
    masked_len = len(phone) - len(prefix) - len(suffix)
    return f"{prefix}{'*' * masked_len}{suffix}"

def _gym_owner_required(view_fn):
    """
    Stricter than permission_required — credential-editing actions are
    gym_owner-only (or superadmin), matching the spec's explicit rule
    that owners must never be able to delegate this away, even via a
    togglable StaffPermission flag. Mirrors the style of
    AuthFit.views._gym_staff_required / _gym_role_required exactly.
    """
    from AuthFit.views import _gym_staff_required

    @_gym_staff_required
    @functools.wraps(view_fn)
    def wrapped(request, *args, **kwargs):
        if not (request.is_super_admin or request.staff_role == 'gym_owner'):
            raise PermissionDenied("Only the gym owner can manage WhatsApp settings.")
        return view_fn(request, *args, **kwargs)
    return wrapped


@_gym_owner_required
def whatsapp_settings(request):
    """
    GET/POST /owner/whatsapp/settings/
    Setup-wizard form: business identity + credentials. Saving here does
    NOT flip `enabled` or call Meta — that only happens via the explicit
    Verify/Connect action below, so an owner can save partial progress
    without accidentally going "live."
    """
    gym = getattr(request, 'gym', None)
    if gym is None:
        raise PermissionDenied("No gym context available.")

    settings_row, _ = GymWhatsAppSettings.objects.get_or_create(
        gym=gym,
        defaults={"business_name": gym.gym_name},
    )

    if request.method == "POST":
        form = WhatsAppSettingsForm(request.POST, instance=settings_row)
        if form.is_valid():
            form.save()
            messages.success(request, "WhatsApp settings saved. Click 'Verify & Connect' to activate.")
            return redirect('whatsapp_settings')
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = WhatsAppSettingsForm(instance=settings_row)

    return render(request, "gym_settings_whatsapp.html", {
        "gym": gym,
        "form": form,
        "wa_settings": settings_row,
    })


@_gym_owner_required
@require_POST
def whatsapp_verify(request):
    gym = getattr(request, 'gym', None)
    if gym is None:
        raise PermissionDenied("No gym context available.")

    try:
        result = whatsapp_service.verify_connection(gym)
    except WhatsAppError as exc:
        messages.error(request, f"WhatsApp verification failed: {exc}")
        return redirect('whatsapp_settings')

    if result.success:
        settings_row = gym.whatsapp_settings
        if not settings_row.enabled:
            settings_row.enabled = True
            settings_row.save(update_fields=['enabled', 'updated_at'])
        templates_found = result.response.get("templates_found", [])
        messages.success(
            request,
            f"WhatsApp connected — {len(templates_found)} approved template(s) found. "
            "It's now active for this gym."
        )
    else:
        missing = result.response.get("missing_templates")
        if missing:
            messages.error(
                request,
                "WhatsApp verification failed — missing approved template(s): "
                + ", ".join(missing) +
                ". Create and get these approved in Meta Business Manager, then verify again."
            )
        else:
            messages.error(request, f"WhatsApp verification failed: {result.error}")

    return redirect('whatsapp_settings')

@_gym_owner_required
@require_POST
def whatsapp_disconnect(request):
    """POST /owner/whatsapp/disconnect/ — explicit opt-out via the
    model's mark_disconnected() helper (flips enabled=False too)."""
    gym = getattr(request, 'gym', None)
    if gym is None:
        raise PermissionDenied("No gym context available.")

    try:
        settings_row = gym.whatsapp_settings
    except GymWhatsAppSettings.DoesNotExist:
        messages.error(request, "WhatsApp was never configured for this gym.")
        return redirect('whatsapp_settings')

    settings_row.mark_disconnected()
    messages.success(request, "WhatsApp disconnected. No further messages will be sent until reconnected.")
    return redirect('whatsapp_settings')


@permission_required("can_send_whatsapp")
@require_POST
def whatsapp_send_test(request):
    """
    POST /owner/whatsapp/send-test/
    Body (form-encoded): phone
 
    Accepts either a bare 10-digit number ("9876543210") or full E.164
    ("+919876543210") — normalized here via the same
    normalize_phone_to_e164() the notification layer already uses (Step
    4), so there's one implementation of "how do we turn what a user
    typed into E.164," not a second one duplicated in this view. The
    service layer (_validate_phone inside send_test_message) still does
    the actual E.164 validation afterward — this view only improves what
    gets handed to it, it does not relax or duplicate that check.
    """
    gym = getattr(request, 'gym', None)
    if gym is None:
        return JsonResponse({"success": False, "error": "No gym context available."}, status=403)
 
    raw_phone = request.POST.get("phone", "").strip()
    if not raw_phone:
        return JsonResponse({"success": False, "error": "Phone number is required."}, status=400)
 
    to_phone = whatsapp_service.normalize_phone_to_e164(raw_phone)
 
    try:
        result = whatsapp_service.send_test_message(gym, to_phone)
    except WhatsAppRateLimitExceeded as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=429)
    except WhatsAppInvalidPhoneNumber as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except (WhatsAppNotConfigured, WhatsAppDisabled) as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except WhatsAppError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=500)
 
    if result.success:
        return JsonResponse({"success": True, "message_id": result.message_id})
    return JsonResponse({"success": False, "error": result.error}, status=502)

@permission_required("can_send_whatsapp")
def whatsapp_status(request):
    """
    GET /owner/whatsapp/status/  — JSON status for the dashboard widget.
    Read-only, no side effects.
 
    CHANGED:
      - `phone_number` is now MASKED (_mask_phone) — a receptionist with
        only can_send_whatsapp should never see the gym's full registered
        WhatsApp business number in a JSON response; only the owner sees
        it unmasked, on the settings FORM itself (whatsapp_settings(),
        which is _gym_owner_required and renders the real form field).
      - `ready` is a NEW computed field: True only when the connection is
        both enabled AND actually healthy (status == 'connected') —
        evaluated from existing model state (settings_row.enabled,
        settings_row.status), no new DB field. This is deliberately
        STRICTER than GymWhatsAppSettings.is_operational (which, per Step
        3.5's design, is just `enabled` — the gate the SEND path uses so a
        transient error doesn't block retries). `ready` is a UI signal
        for the dashboard's green/red badge, not a send-path gate; the
        two are allowed to disagree (e.g. right after a transient error,
        sends keep being attempted per is_operational, but the badge
        correctly shows red via `ready` until a send/verify succeeds and
        self-heals status back to 'connected').
    """
    gym = getattr(request, 'gym', None)
    if gym is None:
        return JsonResponse({"error": "No gym context available."}, status=403)
 
    try:
        settings_row = gym.whatsapp_settings
    except GymWhatsAppSettings.DoesNotExist:
        return JsonResponse({
            "configured": False, "enabled": False, "status": "not_configured", "ready": False,
        })
 
    return JsonResponse({
        "configured": True,
        "enabled": settings_row.enabled,
        "status": settings_row.status,
        "status_display": settings_row.get_status_display(),
        "ready": settings_row.enabled and settings_row.status == 'connected',
        "verified_at": settings_row.verified_at.isoformat() if settings_row.verified_at else None,
        "last_error": settings_row.last_error,
        "business_name": settings_row.business_name,
        "phone_number": _mask_phone(settings_row.phone_number),
    })
import logging
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from AuthFit.attendance import mark_qr_attendance

logger = logging.getLogger(__name__)

SESSION_KEY = "pending_qr_token"
INTENT_MAX_AGE = 300  # 5 minutes
signer = TimestampSigner(salt="qr-attendance-intent")


def _rate_limited(request, qr_token):
    ip = request.META.get('REMOTE_ADDR', 'unknown')
    key = f"qr_entry_rl_{ip}_{qr_token[:12]}"
    count = cache.get(key, 0)
    if count >= 20:
        return True
    cache.set(key, count + 1, timeout=60)
    return False


def _mark_consumed(signed_value):
    key = f"qr_intent_consumed_{signed_value[-32:]}"
    if cache.get(key):
        return True
    cache.set(key, True, timeout=INTENT_MAX_AGE)
    return False


@require_http_methods(["GET"])
def qr_attendance_entry(request, qr_token):
    """
    Universal QR entry point — works from phone camera, Google Lens,
    Google scanner, or the app's deep link handler.
    /attendance/qr/<token>/
    """
    if _rate_limited(request, qr_token):
        logger.warning(
            "QR entry rate-limited: ip=%s token_prefix=%s",
            request.META.get('REMOTE_ADDR'), qr_token[:12],
        )
        return render(request, "qr_entry_result.html", {
            "status": "error",
            "message": "Too many attempts. Please wait a moment and try again.",
        }, status=429)

    if not request.user.is_authenticated:
        # Signed + timestamped so it's tamper-proof and short-lived.
        # Token validity itself is checked inside mark_qr_attendance
        # after login, avoiding a duplicate GymQRCode lookup here.
        request.session[SESSION_KEY] = signer.sign(qr_token)
        login_url = reverse('login')
        # `next` always points at our own fixed resume view — never
        # client-controlled — so this can't become an open redirect.
        return redirect(f"{login_url}?next={reverse('qr_attendance_resume')}")

    return _process_and_render(request, qr_token)


@login_required
@require_http_methods(["GET"])
def qr_attendance_resume(request):
    """
    Landed here after login (via ?next=). Pulls the pending token back
    out of session, validates it, and finishes attendance — no re-scan.
    """
    raw = request.session.pop(SESSION_KEY, None)
    if not raw:
        return redirect('Attendence')

    if _mark_consumed(raw):
        logger.warning("QR intent replay attempt: user_id=%s", request.user.id)
        return render(request, "qr_entry_result.html", {
            "status": "error",
            "message": "This QR session was already used. Please scan the QR code again.",
        })

    try:
        qr_token = signer.unsign(raw, max_age=INTENT_MAX_AGE)
    except SignatureExpired:
        logger.info("QR intent expired for user_id=%s", request.user.id)
        return render(request, "qr_entry_result.html", {
            "status": "error",
            "message": "That QR session expired. Please scan the QR code again.",
        })
    except BadSignature:
        logger.warning("QR intent bad signature for user_id=%s", request.user.id)
        return render(request, "qr_entry_result.html", {
            "status": "error",
            "message": "Something went wrong. Please scan the QR code again.",
        })

    return _process_and_render(request, qr_token)


def _process_and_render(request, qr_token):
    result = mark_qr_attendance(request.user, qr_token)
    # Flash the result once via session, then redirect to the existing
    # dashboard — a refresh won't re-trigger the popup.
    request.session['qr_result_flash'] = result
    return redirect('Attendence')
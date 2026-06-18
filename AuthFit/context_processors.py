# AuthFit/context_processors.py

import hmac
import hashlib
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


def _user_hash(uid: int) -> str:
    """
    Short HMAC of user ID — safe to expose in JS (not reversible to user.id).
    Used by the Service Worker to scope its cache without exposing PII.
    """
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        str(uid).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]


def gym_config(request):
    """
    Injects GYM_CONFIG_JSON into every template response.

    Exposes to JS:
        isAuthenticated, isEnrolled, alreadyMarked, userHash

    Never exposes:
        user.id, gym coordinates, gym radius, gym_id

    Multi-tenancy:
        All cache keys are scoped to (uid, gym_pk) so a user
        enrolled at two gyms gets correct per-gym state.
    """

    is_enrolled = False
    already_marked = False
    user_hash = ""

    if not request.user.is_authenticated:
        return _build_response(is_enrolled, already_marked, user_hash)

    uid = request.user.id
    today = timezone.localdate()

    # gym is set by GymMiddleware — None for superusers
    gym = getattr(request, 'gym', None)
    gym_pk = gym.pk if gym else 'none'

    # ── User hash ──────────────────────────────────────────────────────────
    try:
        user_hash = _user_hash(uid)
    except Exception:
        logger.exception("Failed generating user hash for user %s", uid)

    # ── Enrollment check ───────────────────────────────────────────────────
    # Key matches geo_views.py and views.py so all three share one cache entry
    enroll_key = f"enrollment_status_{uid}_{gym_pk}"
    enroll_data = cache.get(enroll_key)

    if enroll_data is None:
        try:
            from AuthFit.models import Enrollment

            qs = Enrollment.objects.filter(user=request.user)
            if gym:
                qs = qs.filter(gym=gym)

            enrollment = qs.first()
            enroll_data = (
                {"exists": True,  "expired": bool(enrollment.is_expired)}
                if enrollment else
                {"exists": False, "expired": False}
            )
            cache.set(enroll_key, enroll_data, timeout=300)

        except Exception:
            logger.exception("Enrollment check failed for user %s", uid)
            enroll_data = {"exists": False, "expired": False}

    is_enrolled = (
        bool(enroll_data.get("exists")) and
        not bool(enroll_data.get("expired"))
    )

    # ── Attendance check (only if enrolled) ───────────────────────────────
    # Key matches geo_views.py so the geo endpoint's cache.set is reused here
    if is_enrolled:
        att_key = f"att_marked_{uid}_{gym_pk}_{today}"
        already_marked = cache.get(att_key)

        if already_marked is None:
            try:
                from AuthFit.models import Attendence

                qs = Attendence.objects.filter(user=request.user, date=today)
                if gym:
                    qs = qs.filter(gym=gym)

                already_marked = qs.exists()
                cache.set(
                    att_key,
                    already_marked,
                    timeout=86400 if already_marked else 60,
                )

            except Exception:
                logger.exception("Attendance check failed for user %s", uid)
                already_marked = False

    return _build_response(is_enrolled, already_marked, user_hash, request)


def _build_response(is_enrolled, already_marked, user_hash, request=None):
    """Builds the template context dict."""
    config = {
        "isAuthenticated": request.user.is_authenticated if request else False,
        "isEnrolled":      bool(is_enrolled),
        "alreadyMarked":   bool(already_marked),
        "userHash":        user_hash,
    }
    return {
        "GYM_CONFIG_JSON": json.dumps(config),
        "is_enrolled":     bool(is_enrolled),
        "already_marked":  bool(already_marked),
    }

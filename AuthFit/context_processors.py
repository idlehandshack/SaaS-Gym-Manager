# AuthFit/context_processors.py

import hmac
import hashlib
import json
import logging
import os
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from Gym.branding import get_gym_branding
from cloudinary.utils import cloudinary_url

logger = logging.getLogger(__name__)

def saas_config(request):
    return {
        'BASE_DOMAIN': os.environ.get('BASE_DOMAIN', 'entergym.in'),
    }

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


def gym_context(request):
    """
    Injects gym-wide context into every template automatically.
    Replaces the manual favicon_url / logo_url computation in every view.
    
    Provides:
        {{ gym }}         — the current Gym instance (or None on root domain)
        {{ favicon_url }} — Cloudinary 32x32 favicon URL (or None → base.html falls back to /favicon.ico)
        {{ logo_url }}    — Cloudinary logo URL (or None)
    """
    gym = getattr(request, 'gym', None)

    if gym is None:
        return {'gym': None, 'favicon_url': None, 'logo_url': None}

    # ── Favicon ───────────────────────────────────────────────────────────────
    favicon_url = None
    favicon_cache_key = f"gym_favicon_{gym.pk}"
    favicon_url = cache.get(favicon_cache_key)

    if favicon_url is None and gym.favicon:
        try:
            public_id = (
                gym.favicon.public_id
                if hasattr(gym.favicon, 'public_id')
                else str(gym.favicon)
            )
            if public_id:
                favicon_url, _ = cloudinary_url(
                    public_id,
                    width=32, height=32,
                    crop="fill",
                    fetch_format="auto",
                    quality="auto",
                    secure=True,
                )
                cache.set(favicon_cache_key, favicon_url, timeout=86400)  # 24 h
        except Exception:
            logger.exception("Cloudinary favicon URL error for gym %s", gym.pk)

    # ── Logo ──────────────────────────────────────────────────────────────────
    logo_url = None
    logo_cache_key = f"gym_logo_{gym.pk}"
    logo_url = cache.get(logo_cache_key)

    if logo_url is None and gym.logo:
        try:
            public_id = (
                gym.logo.public_id
                if hasattr(gym.logo, 'public_id')
                else str(gym.logo)
            )
            if public_id:
                logo_url, _ = cloudinary_url(
                    public_id,
                    width=200, height=80,
                    crop="fit",
                    fetch_format="auto",
                    quality="auto",
                    secure=True,
                )
                cache.set(logo_cache_key, logo_url, timeout=86400)  # 24 h
        except Exception:
            logger.exception("Cloudinary logo URL error for gym %s", gym.pk)

    return {
        'gym': gym,
        'favicon_url': favicon_url,
        'logo_url': logo_url,
    }


def gym_branding(request):
    b = get_gym_branding(getattr(request, 'gym', None))
    return {
        "logo_url": b["logo_url"],
        "favicon_url": b["favicon_url"],
        "apple_touch_icon_url": b["apple_touch_icon_url"],
        "splash_logo_url": b["splash_logo_url"],
        "theme_color": b["theme_color"],
        "app_name": b["app_name"],
        "app_short_name": b["app_short_name"],
    }
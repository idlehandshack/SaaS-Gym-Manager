# AuthFit/geo_views.py

import os
import json
import math
import logging

from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.conf import settings
from django.core.cache import cache

from AuthFit.models import Attendence, Enrollment

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Haversine distance
# ──────────────────────────────────────────────────────────────────────────────
def _haversine(lat1, lng1, lat2, lng2):
    R = 6_371_000
    φ1 = math.radians(lat1)
    φ2 = math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lng2 - lng1)
    a = math.sin(Δφ/2)**2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _is_json_request(request):
    ct = request.META.get('HTTP_CONTENT_TYPE',
                          request.META.get('CONTENT_TYPE', ''))
    return 'application/json' in ct


# ──────────────────────────────────────────────────────────────────────────────
# Helper: load gym coordinates from request.gym (set by GymMiddleware)
# Returns (lat, lng, radius) or None if gym has no coords configured
# ──────────────────────────────────────────────────────────────────────────────
def _get_gym_coords(request):
    """
    Pull geo-fence from the Gym model row attached to this request.
    Falls back to env vars only if request.gym is None (superuser/dev).
    """
    gym = getattr(request, 'gym', None)
    if gym is not None:
        return gym.latitude, gym.longitude, gym.radius_meters

    # Fallback for superuser/dev — single-gym env vars
    return (
        float(os.environ.get('GYM_LATITUDE',      21.2179)),
        float(os.environ.get('GYM_LONGITUDE',     81.3311)),
        float(os.environ.get('GYM_RADIUS_METERS', 100)),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Geo attendance endpoint
# ──────────────────────────────────────────────────────────────────────────────
@require_POST
@csrf_exempt
@login_required
def geo_mark_attendance(request):
    """
    POST /api/geo-mark-attendance/
    Accepts: { "lat": float, "lng": float }

    WHY csrf_exempt:
      The Service Worker posts from a background context where injecting
      the CSRF cookie into headers is unreliable. We compensate with:
        1. @login_required  — valid session cookie required
        2. Content-Type: application/json check — blocks form submissions
        3. Per-user rate limiting
    """
    uid = request.user.id
    gym = getattr(request, 'gym', None)

    # ── Reject non-JSON ────────────────────────────────────────────────────
    if not _is_json_request(request):
        return JsonResponse({'status': 'error', 'error': 'JSON required'}, status=415)

    # ── Rate limit: 10 calls/min per user ─────────────────────────────────
    rl_key = f"geo_rl_{uid}"
    calls = cache.get(rl_key, 0)
    if calls >= 10:
        return JsonResponse({'status': 'rate_limited', 'error': 'Too many requests'}, status=429)
    try:
        cache.add(rl_key, 0, timeout=60)
        cache.incr(rl_key)
    except Exception:
        pass

    # ── Parse coordinates ──────────────────────────────────────────────────
    try:
        body = json.loads(request.body)
        lat = float(body['lat'])
        lng = float(body['lng'])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({'status': 'error', 'error': 'Invalid coordinates'}, status=400)

    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return JsonResponse({'status': 'error', 'error': 'Coordinates out of range'}, status=400)

    # ── Enrollment check (cached 5 min, gym-scoped) ───────────────────────
    # Cache key includes gym so a user enrolled at two gyms gets separate entries
    gym_pk = gym.pk if gym else 'none'
    enroll_key = f"enrollment_status_{uid}_{gym_pk}"
    enroll_data = cache.get(enroll_key)

    if enroll_data is None:
        try:
            qs = Enrollment.objects.filter(user=request.user)
            if gym:
                qs = qs.filter(gym=gym)
            enrollment = qs.get()
            enroll_data = {
                'exists':  True,
                'expired': enrollment.is_expired,
                'gym_id':  str(enrollment.gym_id),
            }
            cache.set(enroll_key, enroll_data, timeout=300)
        except Enrollment.DoesNotExist:
            return JsonResponse({
                'status': 'not_enrolled',
                'error':  'Please enroll before marking attendance.',
            }, status=403)
        except Enrollment.MultipleObjectsReturned:
            # User enrolled at multiple gyms — gym context missing
            logger.error(
                "MultipleObjectsReturned for user=%s gym=%s", uid, gym_pk)
            return JsonResponse({
                'status': 'error',
                'error':  'Could not determine gym context.',
            }, status=400)

    if not enroll_data.get('exists'):
        return JsonResponse({'status': 'not_enrolled', 'error': 'Please enroll first.'}, status=403)

    if enroll_data.get('expired'):
        return JsonResponse({'status': 'expired', 'error': 'Your membership has expired. Please renew.'}, status=403)

    # ── Already marked today? ──────────────────────────────────────────────
    today = timezone.localdate()
    att_key = f"att_marked_{uid}_{gym_pk}_{today}"

    if cache.get(att_key):
        return JsonResponse({'status': 'exists', 'message': 'Attendance already marked today.'})

    # ── Distance check against THIS gym's coordinates ──────────────────────
    gym_lat, gym_lng, gym_radius = _get_gym_coords(request)

    # Guard against unconfigured gym (default 0.0, 0.0)
    if gym_lat == 0.0 and gym_lng == 0.0:
        logger.warning("Gym %s has no coordinates configured", gym_pk)
        return JsonResponse({
            'status': 'error',
            'error':  'Gym location not configured. Contact the gym owner.',
        }, status=503)

    distance = _haversine(lat, lng, gym_lat, gym_lng)
    if distance > gym_radius:
        return JsonResponse({
            'status':   'out_of_range',
            'message':  'You are not within the gym premises.',
            'distance': round(distance),
        }, status=403)

    # ── Mark attendance — gym-scoped ───────────────────────────────────────
    try:
        _, created = Attendence.objects.get_or_create(
            user=request.user,
            date=today,
            gym=gym,                  # ← required NOT NULL field
        )
    except Exception:
        logger.exception(
            "DB error in geo_mark_attendance user=%s gym=%s", uid, gym_pk)
        return JsonResponse({'status': 'error', 'error': 'Database error. Try again.'}, status=500)

    # Cache the mark so the next call in the same day is instant
    cache.set(att_key, True, timeout=86400)

    # Bust staff today-attendance list cache (gym-scoped key)
    cache.delete(f"today_attendance_{gym_pk}_{today}")

    if created:
        logger.info("Geo attendance marked: user=%s gym=%s date=%s dist=%sm",
                    uid, gym_pk, today, round(distance))
        return JsonResponse({'status': 'success', 'message': 'Attendance marked!', 'distance': round(distance)})
    else:
        return JsonResponse({'status': 'exists', 'message': 'Attendance already marked today.', 'distance': round(distance)})


# ──────────────────────────────────────────────────────────────────────────────
# Status check (called by SW before sending coordinates)
# ──────────────────────────────────────────────────────────────────────────────
@login_required
@require_GET
def attendance_status(request):
    """GET /api/attendance-status/"""
    uid = request.user.id
    gym = getattr(request, 'gym', None)
    gym_pk = gym.pk if gym else 'none'

    enroll_key = f"enrollment_status_{uid}_{gym_pk}"
    enroll_data = cache.get(enroll_key)

    if enroll_data is None:
        try:
            qs = Enrollment.objects.filter(user=request.user)
            if gym:
                qs = qs.filter(gym=gym)
            enrollment = qs.get()
            enroll_data = {
                'exists':  True,
                'expired': enrollment.is_expired,
            }
        except Enrollment.DoesNotExist:
            enroll_data = {'exists': False, 'expired': False}
        except Enrollment.MultipleObjectsReturned:
            enroll_data = {'exists': False, 'expired': False}

        cache.set(enroll_key, enroll_data, timeout=300)

    is_enrolled = enroll_data.get('exists') and not enroll_data.get('expired')
    if not is_enrolled:
        return JsonResponse({'marked': False, 'enrolled': False})

    today = timezone.localdate()
    att_key = f"att_marked_{uid}_{gym_pk}_{today}"
    marked = cache.get(att_key)

    if marked is None:
        marked = Attendence.objects.filter(
            user=request.user,
            date=today,
            gym=gym,
        ).exists()
        cache.set(att_key, marked, timeout=86400 if marked else 60)

    return JsonResponse({'marked': bool(marked), 'enrolled': True})


# ──────────────────────────────────────────────────────────────────────────────
# Serve SW from root (/sw.js)
# ──────────────────────────────────────────────────────────────────────────────
def serve_sw(request):
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'js', 'sw.js')
    real_sw = os.path.realpath(sw_path)
    real_base = os.path.realpath(str(settings.BASE_DIR))

    if not real_sw.startswith(real_base + os.sep):
        return HttpResponse('// forbidden', content_type='application/javascript', status=403)

    try:
        with open(real_sw, 'r') as f:
            content = f.read()
        response = HttpResponse(content, content_type='application/javascript')
        response['Service-Worker-Allowed'] = '/'
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    except FileNotFoundError:
        return HttpResponse('// sw.js not found', content_type='application/javascript', status=404)

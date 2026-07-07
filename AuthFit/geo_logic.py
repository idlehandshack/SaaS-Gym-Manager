# AuthFit/geo_logic.py
#
# Pure geo-fence + enrollment/attendance logic, shared by mark_attendance_api.
# No HTTP concerns here — just returns a plain dict the view can JSON-ify.

import math
import logging

from django.utils import timezone
from django.core.cache import cache

from AuthFit.models import Attendence, Enrollment

logger = logging.getLogger(__name__)


def _haversine(lat1, lng1, lat2, lng2):
    R = 6_371_000
    φ1 = math.radians(lat1)
    φ2 = math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lng2 - lng1)
    a = math.sin(Δφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_gym_coords(gym):
    """Pull geo-fence from the Gym row. No env-var fallback for real gyms —
    an unconfigured gym is a data problem, not a config default."""
    if gym is not None and gym.latitude and gym.longitude:
        return gym.latitude, gym.longitude, gym.radius_meters
    return None


def mark_geo_attendance(user, gym, lat, lng):
    """
    Shared geo-fenced attendance flow, used by BOTH:
      - manual browser tap (geo_attendance.js)
      - background Service Worker auto-mark

    Returns a dict with a 'status' key matching the existing contract:
    success | exists | out_of_range | not_enrolled | expired | error
    """
    uid = user.id
    gym_pk = gym.pk if gym else 'none'

    # ── Rate limit: 10 calls/min per user ──────────────────────────
    rl_key = f"geo_rl_{uid}"
    calls = cache.get(rl_key, 0)
    if calls >= 10:
        return {'status': 'rate_limited', 'error': 'Too many requests'}
    try:
        cache.add(rl_key, 0, timeout=60)
        cache.incr(rl_key)
    except Exception:
        pass

    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return {'status': 'error', 'error': 'Coordinates out of range'}

    # ── Enrollment check (cached 5 min, gym-scoped) ────────────────
    enroll_key = f"enrollment_status_{uid}_{gym_pk}"
    enroll_data = cache.get(enroll_key)

    if enroll_data is None:
        try:
            qs = Enrollment.objects.filter(user=user)
            if gym:
                qs = qs.filter(gym=gym)
            enrollment = qs.get()
            enroll_data = {'exists': True, 'expired': enrollment.is_expired}
            cache.set(enroll_key, enroll_data, timeout=300)
        except Enrollment.DoesNotExist:
            return {'status': 'not_enrolled', 'error': 'Please enroll before marking attendance.'}
        except Enrollment.MultipleObjectsReturned:
            logger.error("MultipleObjectsReturned for user=%s gym=%s", uid, gym_pk)
            return {'status': 'error', 'error': 'Could not determine gym context.'}

    if not enroll_data.get('exists'):
        return {'status': 'not_enrolled', 'error': 'Please enroll first.'}
    if enroll_data.get('expired'):
        return {'status': 'expired', 'error': 'Your membership has expired. Please renew.'}

    # ── Already marked today? ───────────────────────────────────────
    today = timezone.localdate()
    att_key = f"att_marked_{uid}_{gym_pk}_{today}"
    if cache.get(att_key):
        return {'status': 'exists', 'message': 'Attendance already marked today.'}

    # ── Geo-fence check ─────────────────────────────────────────────
    coords = get_gym_coords(gym)
    if coords is None:
        logger.warning("Gym %s has no coordinates configured", gym_pk)
        return {'status': 'error', 'error': 'Gym location not configured. Contact the gym owner.'}

    gym_lat, gym_lng, gym_radius = coords
    distance = _haversine(lat, lng, gym_lat, gym_lng)
    if distance > gym_radius:
        return {'status': 'out_of_range', 'message': 'You are not within the gym premises.', 'distance': round(distance)}

    # ── Mark attendance (same table/shape as face-attendance path) ──
    try:
        _, created = Attendence.objects.get_or_create(user=user, date=today, gym=gym)
    except Exception:
        logger.exception("DB error in mark_geo_attendance user=%s gym=%s", uid, gym_pk)
        return {'status': 'error', 'error': 'Database error. Try again.'}

    cache.set(att_key, True, timeout=86400)
    cache.delete(f"today_attendance_{gym_pk}_{today}")

    if created:
        logger.info("Geo attendance marked: user=%s gym=%s date=%s dist=%sm", uid, gym_pk, today, round(distance))
        return {'status': 'success', 'message': 'Attendance marked!', 'distance': round(distance)}
    return {'status': 'exists', 'message': 'Attendance already marked today.', 'distance': round(distance)}
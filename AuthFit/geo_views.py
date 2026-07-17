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
# Status check (called by SW before sending coordinates)
# ──────────────────────────────────────────────────────────────────────────────
@login_required
@require_GET
def attendance_status(request):
    """GET /api/attendance-status/"""
    uid = request.user.id
    gym = getattr(request, 'gym', None)
    gym_pk = gym.pk if gym else 'none'

    if gym is None or not gym.enable_geo_attendance:
        return JsonResponse({'enrolled': False, 'marked': False,"geo_enabled": False,})  # tells JS: don't start tracking

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

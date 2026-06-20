# Gym/middleware.py

import os
import re
from django.core.exceptions import PermissionDenied
from .models import Gym
from django.conf import settings


class GymMiddleware:
    """
    Resolves the gym tenant from the request hostname on every request.

    Resolution order:
        1. Production subdomain:  fitzone.entergym.in  → gym_code='fitzone'
        2. Render bare URL:       saas-gym-manager.onrender.com → None (no gym)
       2b. Dev IP address:        192.168.x.x          → DEV_GYM_CODE env var
        3. Dev subdomain:         fitzone.localhost     → gym_code='fitzone'
        4. Dev env fallback:      localhost             → DEV_GYM_CODE env var

    Sets on request:
        request.gym            — Gym instance or None
        request.staff_role     — 'gym_owner'|'trainer'|'receptionist'|'member'|None
        request.is_super_admin — True only for Django superusers
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.gym            = None
        request.staff_role     = None
        request.is_super_admin = False

        if request.user.is_authenticated and request.user.is_superuser:
            request.is_super_admin = True
            return self.get_response(request)

        gym = self._resolve_gym(request)
        request.gym = gym

        if request.user.is_authenticated and gym:
            exempt_paths = ('/logout/', '/billing/', '/subscription/')
            if not gym.is_subscription_active:
                if not any(request.path.startswith(p) for p in exempt_paths):
                    raise PermissionDenied("Your gym subscription has expired.")

            self._resolve_role(request, gym)

        return self.get_response(request)

    def _resolve_gym(self, request):
        host = request.get_host().split(':')[0].lower()

        # ── 1. Production: fitzone.entergym.in ───────────────────────────
        base_domain = os.environ.get('BASE_DOMAIN', 'entergym.in')
        if host.endswith('.' + base_domain):
            slug = host[:-(len(base_domain) + 1)]
            return Gym.objects.filter(gym_code=slug, active=True).first()

        # ── 2. Render bare URL: saas-gym-manager.onrender.com ────────────
        render_host = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '')
        if render_host and host == render_host.lower():
            return None
        
        if host == 'localhost':
            return None
        # ── 2b. Dev: raw IP address (e.g. 192.168.x.x) ───────────────────
        # Only active when DEBUG=True — zero production risk.
        # No query param support: mirrors production gym-specific app behaviour.
        if settings.DEBUG and re.match(r'^\d{1,3}(\.\d{1,3}){3}$', host):
            dev_code = os.environ.get('DEV_GYM_CODE', '').strip()
            if dev_code:
                return Gym.objects.filter(
                    gym_code=dev_code,
                    active=True
                ).first()

            # localhost should be SaaS
            if host == "localhost":
                return None
            return None

        # ── 3. Dev: fitzone.localhost ─────────────────────────────────────
        if host.endswith('.localhost'):
            slug = host.rsplit('.localhost', 1)[0]
            return Gym.objects.filter(gym_code=slug, active=True).first()

        # ── 4. Dev fallback: plain localhost → DEV_GYM_CODE env var ──────
        dev_code = os.environ.get('DEV_GYM_CODE', '').strip()
        if dev_code:
            return Gym.objects.filter(gym_code=dev_code, active=True).first()

        return None

    def _resolve_role(self, request, gym):
        """
        Sets request.staff_role from StaffProfile or Enrollment.
        Called only for authenticated users with a resolved gym.
        """
        try:
            profile = request.user.staff_profile
            if profile.gym_id == gym.pk and profile.active:
                request.staff_role = profile.role
                return
        except Exception:
            pass

        try:
            from AuthFit.models import Enrollment  # avoid circular import
            if Enrollment.objects.filter(user=request.user, gym=gym).exists():
                request.staff_role = 'member'
        except Exception:
            pass
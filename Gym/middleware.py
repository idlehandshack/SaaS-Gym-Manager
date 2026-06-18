# Gym/middleware.py

import os
from django.core.exceptions import PermissionDenied
from .models import Gym


class GymMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Defaults
        request.gym = None
        request.staff_role = None
        request.is_super_admin = False

        # Superuser — cross-gym access, no gym scoping
        if request.user.is_authenticated and request.user.is_superuser:
            request.is_super_admin = True
            return self.get_response(request)

        # ── Resolve gym from subdomain FIRST (always, for all users) ────
        gym = self._resolve_gym(request)
        request.gym = gym

        # ── Resolve role only for authenticated users ────────────────────
        if request.user.is_authenticated and gym:

            # Check subscription (exempt certain paths)
            exempt_paths = ('/logout/', '/billing/')
            if not gym.is_subscription_active and not any(
                request.path.startswith(p) for p in exempt_paths
            ):
                raise PermissionDenied("Gym subscription is inactive.")

            # Staff role
            try:
                profile = request.user.staff_profile
                if profile.gym_id == gym.pk:
                    request.staff_role = profile.role
            except Exception:
                pass

            # Member role (has enrollment at this gym)
            if not request.staff_role:
                from AuthFit.models import Enrollment
                if Enrollment.objects.filter(
                    user=request.user, gym=gym
                ).exists():
                    request.staff_role = 'member'

        return self.get_response(request)

    def _resolve_gym(self, request):
        host = request.get_host().split(':')[0]
        parts = host.split('.')
        
        print(f"[DEBUG] host={host} parts={parts}")  # ← ADD THIS

        # Production: fitzone.entergym.in → slug = fitzone
        if len(parts) >= 3 and parts[-1] != 'localhost':
            slug = parts[0]
            print(f"[DEBUG] production path, slug={slug}")  # ← ADD THIS
            try:
                return Gym.objects.get(gym_code=slug)
            except Gym.DoesNotExist:
                print(f"[DEBUG] Gym not found for slug={slug}")  # ← ADD THIS
                return None

        # Dev: fitzone.localhost → slug = fitzone
        if len(parts) == 2 and parts[1] == 'localhost':
            slug = parts[0]
            print(f"[DEBUG] dev path, slug={slug}")  # ← ADD THIS
            try:
                gym = Gym.objects.get(gym_code=slug)
                print(f"[DEBUG] found gym={gym}")  # ← ADD THIS
                return gym
            except Gym.DoesNotExist:
                print(f"[DEBUG] Gym not found for slug={slug}")  # ← ADD THIS
                return None

        return None
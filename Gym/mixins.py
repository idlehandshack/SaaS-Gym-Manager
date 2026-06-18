"""
gyms/mixins.py
--------------
Reusable view mixins and a base queryset manager that enforce gym-scoped
data access throughout the entire application.

Usage in views:
    class EnrollmentListView(GymQuerysetMixin, ListView):
        model = Enrollment

Usage in DRF viewsets:
    class EnrollmentViewSet(GymScopedMixin, ModelViewSet):
        queryset = Enrollment.objects.all()
"""

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.db.models import Manager, QuerySet
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import PermissionDenied as DrfDenied

# ──────────────────────────────────────────────────────────────────────────────
# Manager / QuerySet helpers
# ──────────────────────────────────────────────────────────────────────────────
class GymQuerySet(QuerySet):
    def for_gym(self, gym):
        """Filter to a specific gym.  gym=None raises PermissionDenied."""
        if gym is None:
            raise PermissionDenied('No gym context available.')
        return self.filter(gym=gym)


class GymManager(Manager):
    def get_queryset(self):
        return GymQuerySet(self.model, using=self._db)

    def for_gym(self, gym):
        return self.get_queryset().for_gym(gym)


# ──────────────────────────────────────────────────────────────────────────────
# Django Class-Based View Mixins
# ──────────────────────────────────────────────────────────────────────────────
class GymRequiredMixin:
    """
    Ensures a gym is present on the request.
    Super-admins bypass this check (they see cross-gym views instead).
    """
    def dispatch(self, request, *args, **kwargs):
        if not request.is_super_admin and request.gym is None:
            raise PermissionDenied('You are not associated with any gym.')
        return super().dispatch(request, *args, **kwargs)


class GymQuerysetMixin(GymRequiredMixin):
    """
    Automatically scopes get_queryset() to request.gym.
    Use as the first base class in your view.
    """
    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.is_super_admin:
            # Super-admin can optionally filter by ?gym=<gym_code>
            gym_code = self.request.GET.get('gym')
            if gym_code:
                return qs.filter(gym__gym_code=gym_code)
            return qs
        return qs.filter(gym=self.request.gym)

    def get_object(self):
        """Override to prevent IDOR: always scope to the current gym."""
        obj = super().get_object()
        if not self.request.is_super_admin:
            gym = getattr(obj, 'gym', None)
            if gym != self.request.gym:
                raise Http404
        return obj


class RoleRequiredMixin:
    """
    Restrict a view to specific roles.
    Set `allowed_roles` on the view class.

    Example:
        class FinanceView(RoleRequiredMixin, GymQuerysetMixin, ListView):
            allowed_roles = ['gym_owner']
    """
    allowed_roles: list = []

    def dispatch(self, request, *args, **kwargs):
        if request.is_super_admin:
            return super().dispatch(request, *args, **kwargs)
        if request.staff_role not in self.allowed_roles:
            raise PermissionDenied('You do not have permission for this action.')
        return super().dispatch(request, *args, **kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# DRF Mixins
# ──────────────────────────────────────────────────────────────────────────────
class GymScopedMixin:
    """
    DRF ViewSet mixin.
    - Scopes get_queryset() to request.gym.
    - Injects gym into serializer.save() automatically.
    - Blocks IDOR on retrieve/update/destroy.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        request = self.request
        if getattr(request, 'is_super_admin', False):
            gym_code = request.query_params.get('gym')
            if gym_code:
                return qs.filter(gym__gym_code=gym_code)
            return qs
        gym = getattr(request, 'gym', None)
        if gym is None:
            
            raise DrfDenied('No gym context.')
        return qs.filter(gym=gym)

    def perform_create(self, serializer):
        serializer.save(gym=self.request.gym)

    def get_object(self):
        obj = super().get_object()
        if not getattr(self.request, 'is_super_admin', False):
            if getattr(obj, 'gym', None) != self.request.gym:
               
                raise NotFound
        return obj
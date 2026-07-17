"""
Gym/services/orphan_users.py
-----------------------------
Query + validation logic for the Super Admin "Orphan User Cleanup" tool.

An orphan user = signed up but has zero relationship to any gym's
business data (no Enrollment, no owned Gym, no StaffProfile — which
covers Owner/Trainer/Receptionist roles), and isn't a Django
superuser/staff account.

Trainer note: AuthFit.models.Trainer has no FK to auth.User in this
codebase (it's a plain roster entry: name/phone/gender/charge), so a
"user is a Trainer" check is expressed via StaffProfile(role='trainer')
instead of a separate Trainer lookup.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from AuthFit.models import Enrollment
from Gym.models import Gym, StaffProfile

DEFAULT_MIN_AGE_DAYS = 7


def orphan_users_base_queryset(min_age_days: int = DEFAULT_MIN_AGE_DAYS):
    """
    Returns an annotated queryset of orphan-candidate users.
    Uses Exists() subqueries — no N+1, no Python-side loops.
    """
    cutoff = timezone.now() - timedelta(days=min_age_days)

    enrollment_exists = Enrollment.objects.filter(user=OuterRef('pk'))
    owned_gym_exists   = Gym.objects.filter(owner=OuterRef('pk'))
    staff_profile_exists = StaffProfile.objects.filter(user=OuterRef('pk'))

    qs = (
        User.objects
        .annotate(
            has_enrollment=Exists(enrollment_exists),
            has_owned_gym=Exists(owned_gym_exists),
            has_staff_profile=Exists(staff_profile_exists),
        )
        .filter(
            has_enrollment=False,
            has_owned_gym=False,
            has_staff_profile=False,
            is_superuser=False,
            is_staff=False,
            date_joined__lte=cutoff,
        )
    )
    return qs


def apply_filters(qs, request):
    """Applies list-page search/filter/sort params on top of the base orphan queryset."""
    search = request.GET.get("search", "").strip()
    if search:
        qs = qs.filter(Q(username__icontains=search) | Q(email__icontains=search))

    age_filter = request.GET.get("age_filter", "")
    now = timezone.now()
    if age_filter == "60":
        qs = qs.filter(date_joined__lte=now - timedelta(days=60))
    elif age_filter == "90":
        qs = qs.filter(date_joined__lte=now - timedelta(days=90))
    elif age_filter == "never_logged_in":
        qs = qs.filter(last_login__isnull=True)
    elif age_filter == "logged_in_never_joined":
        qs = qs.filter(last_login__isnull=False)
    # "30" (default retention) needs no extra filter — already baked into base qs.

    sort = request.GET.get("sort", "newest")
    sort_map = {
        "newest": "-date_joined",
        "oldest": "date_joined",
        "date_joined": "-date_joined",
        "last_login": "-last_login",
    }
    qs = qs.order_by(sort_map.get(sort, "-date_joined"))
    return qs


def revalidate_orphan(user_id: int) -> tuple[bool, str]:
    """
    Re-checks a single user against the orphan definition immediately
    before deletion, to guard against race conditions (e.g. the user
    enrolled in the gap between page-load and delete-click).
    Returns (is_still_orphan, reason_if_not).
    """
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return False, "User no longer exists."

    if user.is_superuser or user.is_staff:
        return False, "User is a superuser/staff account."
    if Enrollment.objects.filter(user=user).exists():
        return False, "User has since become enrolled in a gym."
    if Gym.objects.filter(owner=user).exists():
        return False, "User now owns a gym."
    if StaffProfile.objects.filter(user=user).exists():
        return False, "User now has a staff profile (owner/trainer/receptionist)."

    return True, ""
import functools
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from AuthFit.models import Enrollment


def active_member_required(view_fn):
    """
    Guards member-portal views. Attaches request.enrollment (guaranteed
    non-null, is_deleted=False) or renders a clear 'membership inactive'
    page instead of falling through with enrollment=None / 404 / silent
    no-op, depending on which view happened to be hit.
    """
    @login_required
    @functools.wraps(view_fn)
    def wrapped(request, *args, **kwargs):
        gym = getattr(request, 'gym', None)
        enrollment = (
            Enrollment.objects
            .filter(user=request.user, gym=gym, is_deleted=False)
            .select_related('selectPlan', 'trainer')
            .first()
        )
        if enrollment is None:
            return render(request, 'membership_inactive.html', {'gym': gym}, status=403)
        request.enrollment = enrollment
        return view_fn(request, *args, **kwargs)
    return wrapped
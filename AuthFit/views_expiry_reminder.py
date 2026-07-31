# AuthFit/views_expiry_reminder.py
#
# Wire this into AuthFit/urls.py:
#
#   from AuthFit.views_expiry_reminder import (
#       SendExpiryReminderView, SendExpiryReminderPageView,
#   )
#   urlpatterns += [
#       path('api/send-expiry-reminders/', SendExpiryReminderView.as_view(),
#            name='send-expiry-reminders'),
#       path('admin-tools/expiry-reminders/', SendExpiryReminderPageView.as_view(),
#            name='expiry-reminders-page'),
#   ]
#
# NOTE: the API view below is the reservation-pattern version (short
# SELECT FOR UPDATE transaction to reserve the day, notifications sent
# with NO transaction open). It deliberately does NOT hold the row lock
# across send_expiry_reminders() — see the class docstring for why.

import logging

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.utils import timezone
from django.views.generic import TemplateView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Gym.models import Gym

from .notifications import send_expiry_reminders
from .permissions import ALLOWED_EXPIRY_REMINDER_ROLES, IsGymOwnerOrReceptionist

logger = logging.getLogger(__name__)


class SendExpiryReminderView(APIView):
    """
    POST /api/send-expiry-reminders/

    Manually triggers membership expiry reminders for the caller's own
    gym only. Restricted to gym_owner / receptionist. Rate-limited to
    once per calendar day per gym via Gym.last_expiry_reminder_sent_at
    (a plain DB column — no cache, no Redis).

    Tenant isolation: the gym is ALWAYS taken from request.gym (resolved
    server-side from the authenticated staff member's own profile /
    tenant middleware). The gym id is never read from the request body,
    query params, or any client-supplied value.

    ── Lock-duration design ────────────────────────────────────────────
    Sending pushes is network I/O (FCM + Web Push) and can take seconds
    for large gyms, or hang outright on a slow provider. We never want
    that inside a DB transaction. So this view splits into two phases:

      Phase 1 (short, locked)  — "reserve":
          BEGIN
          SELECT ... FOR UPDATE on the Gym row
          check last_expiry_reminder_sent_at
          if already sent today -> COMMIT, return 400 immediately
          otherwise: stamp last_expiry_reminder_sent_at = now() -> COMMIT
          (lock held only for a couple of fast, local queries — never
          dependent on any external service)

      Phase 2 (long, unlocked) — "execute":
          send_expiry_reminders(gym=gym) runs with NO open transaction
          and NO row lock held.

    Concurrency: two simultaneous clicks both try to SELECT FOR UPDATE
    the same row. One blocks briefly until the other's short reservation
    transaction commits, then sees the now-stamped
    last_expiry_reminder_sent_at and returns "already sent today."
    Exactly one request proceeds to Phase 2 — no duplicate sends.

    Failure handling: if Phase 2 raises, the reservation is rolled back
    (cleared) so the day isn't burned by a run that sent nothing.
    """

    permission_classes = [IsAuthenticated, IsGymOwnerOrReceptionist]

    def post(self, request, *args, **kwargs):
        gym_ref = getattr(request, 'gym', None)

        if gym_ref is None:
            logger.error(
                "SendExpiryReminderView: request.gym missing for user=%s",
                request.user.id,
            )
            return Response(
                {
                    "success": False,
                    "message": "Could not determine your gym. Please contact support.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Phase 1: reserve today's slot. Short, locked, DB-only. ─────────
        reserved, gym, already_sent_response = self._reserve_today(gym_ref.pk, request.user.id)
        if not reserved:
            return already_sent_response

        # ── Phase 2: send. Long-running, network I/O, NO transaction open. ─
        try:
            members_notified = send_expiry_reminders(gym=gym)
        except Exception:
            logger.exception(
                "SendExpiryReminderView: send_expiry_reminders failed gym=%s "
                "— clearing reservation so it can be retried today",
                getattr(gym, "gym_code", gym.id),
            )
            self._clear_reservation(gym.pk)
            return Response(
                {
                    "success": False,
                    "message": "Something went wrong while sending reminders. Please try again.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info(
            "Manual expiry reminders sent gym=%s by user=%s members_notified=%d",
            getattr(gym, "gym_code", gym.id),
            request.user.id,
            members_notified,
        )

        return Response(
            {
                "success": True,
                "members_notified": members_notified,
                "message": "Expiry reminders sent successfully.",
            },
            status=status.HTTP_200_OK,
        )

    # ── Phase 1 helper ──────────────────────────────────────────────────────

    @staticmethod
    def _reserve_today(gym_pk, user_id):
        """
        Locks the Gym row, checks the once-per-day rule, and — if the
        slot is free — immediately stamps last_expiry_reminder_sent_at
        and commits. Lock lifetime is exactly this method's body: one
        SELECT FOR UPDATE and, at most, one UPDATE. No network I/O ever
        happens between BEGIN and COMMIT here.

        Returns (reserved: bool, gym: Gym, early_response: Response|None).
        """
        today = timezone.localdate()

        with transaction.atomic():
            gym = Gym.objects.select_for_update().get(pk=gym_pk)

            if gym.last_expiry_reminder_sent_at and timezone.localtime(gym.last_expiry_reminder_sent_at).date() == today:
                logger.info(
                    "SendExpiryReminderView: duplicate trigger blocked gym=%s user=%s",
                    getattr(gym, "gym_code", gym.id), user_id,
                )
                return False, gym, Response(
                    {
                        "success": False,
                        "message": "Expiry reminders have already been sent today.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            gym.last_expiry_reminder_sent_at = timezone.now()
            gym.save(update_fields=["last_expiry_reminder_sent_at"])

        return True, gym, None

    # ── Failure-path helper ─────────────────────────────────────────────────

    @staticmethod
    def _clear_reservation(gym_pk):
        """
        Rolls back the reservation after a failed send, in its own short
        transaction, so a fully-failed run doesn't cost the gym its one
        reminder slot for the day.
        """
        Gym.objects.filter(pk=gym_pk).update(last_expiry_reminder_sent_at=None)


class SendExpiryReminderPageView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """
    GET /admin-tools/expiry-reminders/

    Renders the "Send Expiry Reminder" staff page (AuthFit/expiryremindercard.html).
    This is the page shell only — the button on it POSTs to
    SendExpiryReminderView above, which does the actual work and is the
    single source of truth for the once-per-day rule and tenant
    isolation. This view does not duplicate any of that logic; it only
    needs read access to request.gym to render the current
    "Last Sent" timestamp on load.

    Access control mirrors the API: only gym_owner / receptionist may
    view the page at all (trainers/members get a 403 rather than a
    button they're not allowed to use). LoginRequiredMixin sends
    anonymous users to the login page; UserPassesTestMixin.test_func
    below enforces the role check for anyone who is authenticated but
    not gym_owner/receptionist.
    """

    template_name = "expiryremindercard.html"
    raise_exception = True  # authenticated but wrong role -> 403, not a redirect loop

    def test_func(self):
        staff_profile = getattr(self.request.user, 'staff_profile', None)
        return staff_profile is not None and staff_profile.role in ALLOWED_EXPIRY_REMINDER_ROLES

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Same source of truth as the API view: request.gym, resolved
        # server-side by tenant middleware from the logged-in staff
        # member's own profile. Never anything client-supplied.
        context["gym"] = getattr(self.request, "gym", None)
        return context
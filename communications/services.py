"""
communications/services.py

Implements the spec's `architecture.communication_flow`:

    Super Admin -> Communications Module -> Audience Resolver
                -> Channel Resolver -> Notification Services -> Delivery Log

AudienceResolver turns a CommunicationAudience row into flat recipient
lists. CommunicationDispatcher (the Channel Resolver) fans those out to the
channels the Communication has enabled, calling the EXACT same senders
`announcements` and `AuthFit` already use:

    - Shop.notifications.send_push_to_tokens   (FCM)
    - notifications.utils.send_web_push        (browser / PWA)

No Firebase/VAPID/webpush code is duplicated here — that's the whole point
of the adapter pattern per the spec's `adapter_pattern` section. Email /
WhatsApp / SMS are accepted as channel toggles on the model already, but
there is no sender to adapt to yet, so those are no-ops (logged, not
silently dropped) until Shop/notifications-equivalents exist for them.
"""

import logging
import operator
from collections import defaultdict
from datetime import timedelta
from functools import reduce

from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from AuthFit.models import Enrollment, UserDevice
from Gym.models import Gym, StaffProfile
from Shop.models import StaffDevice
from Shop.notifications import send_push_to_tokens
from notifications.utils import send_web_push

from .models import Communication, CommunicationAudience, CommunicationDeliveryLog, EXPIRING_SOON_WINDOW_DAYS
from .utils import build_push_body

logger = logging.getLogger(__name__)


class AudienceResolver:
    """
    Resolves a CommunicationAudience into two flat, deduplicated recipient
    lists of (user_id, gym_id) pairs:

        member_pairs -> looked up against AuthFit.UserDevice (member FCM)
        staff_pairs  -> looked up against Shop.StaffDevice   (staff FCM)

    Both tables are gym-scoped (gym FK required), which is why every
    recipient is carried alongside the gym it belongs to rather than as a
    bare user id — a user enrolled at two gyms needs two lookups, one per
    gym's token table.
    """

    @staticmethod
    def _narrow_to_gyms(qs, audience, gym_field='gym_id'):
        """If the admin picked specific gyms, narrow any scope to just those."""
        gym_ids = list(audience.gyms.values_list('id', flat=True))
        if gym_ids:
            qs = qs.filter(**{f'{gym_field}__in': gym_ids})
        return qs

    @staticmethod
    def _city_gym_ids(audience):
        cities = [c.strip() for c in (audience.cities or []) if str(c).strip()]
        if not cities:
            return []
        city_filter = reduce(operator.or_, (Q(city__iexact=c) for c in cities))
        return list(Gym.objects.filter(city_filter).values_list('id', flat=True))

    @classmethod
    def _all_members(cls, audience):
        qs = Enrollment.objects.filter(is_deleted=False, user__isnull=False)
        qs = cls._narrow_to_gyms(qs, audience)
        return list(qs.values_list('user_id', 'gym_id').distinct())

    @classmethod
    def _members_filtered(cls, audience, *, is_active=None, due_lt=None, due_gte=None, due_lte=None):
        qs = Enrollment.objects.filter(is_deleted=False, user__isnull=False)
        qs = cls._narrow_to_gyms(qs, audience)
        if is_active is not None:
            qs = qs.filter(is_active=is_active)
        if due_lt is not None:
            qs = qs.filter(DueDate__lt=due_lt)
        if due_gte is not None:
            qs = qs.filter(DueDate__gte=due_gte)
        if due_lte is not None:
            qs = qs.filter(DueDate__lte=due_lte)
        return list(qs.values_list('user_id', 'gym_id').distinct())

    @classmethod
    def _members_by_plan(cls, audience):
        qs = Enrollment.objects.filter(is_deleted=False, user__isnull=False)
        qs = cls._narrow_to_gyms(qs, audience)

        plan_ids = list(audience.plans.values_list('id', flat=True))
        name = (audience.plan_name_filter or '').strip()

        filt = Q(pk__in=[])  # empty by default
        if plan_ids:
            filt |= Q(selectPlan_id__in=plan_ids)
        if name:
            filt |= Q(selectPlan__plan__iexact=name)
        if not plan_ids and not name:
            return []
        return list(qs.filter(filt).values_list('user_id', 'gym_id').distinct())

    @classmethod
    def _members_by_city(cls, audience):
        gym_ids = cls._city_gym_ids(audience)
        if not gym_ids:
            return []
        qs = Enrollment.objects.filter(is_deleted=False, user__isnull=False, gym_id__in=gym_ids)
        return list(qs.values_list('user_id', 'gym_id').distinct())

    @classmethod
    def _specific_members(cls, audience):
        user_ids = list(audience.specific_members.values_list('id', flat=True))
        if not user_ids:
            return []
        qs = Enrollment.objects.filter(is_deleted=False, user_id__in=user_ids)
        qs = cls._narrow_to_gyms(qs, audience)
        return list(qs.values_list('user_id', 'gym_id').distinct())

    @classmethod
    def _all_staff(cls, audience):
        qs = StaffProfile.objects.filter(active=True)
        qs = cls._narrow_to_gyms(qs, audience)
        return list(qs.values_list('user_id', 'gym_id').distinct())

    @classmethod
    def _staff_by_role(cls, audience, role):
        qs = StaffProfile.objects.filter(active=True, role=role)
        qs = cls._narrow_to_gyms(qs, audience)
        return list(qs.values_list('user_id', 'gym_id').distinct())

    @classmethod
    def _staff_by_city(cls, audience):
        gym_ids = cls._city_gym_ids(audience)
        if not gym_ids:
            return []
        qs = StaffProfile.objects.filter(active=True, gym_id__in=gym_ids)
        return list(qs.values_list('user_id', 'gym_id').distinct())

    @classmethod
    def _owners_by_subscription_plan(cls, audience):
        plan_ids = list(audience.subscription_plans.values_list('id', flat=True))
        if not plan_ids:
            return []
        gym_ids = list(Gym.objects.filter(plan_id__in=plan_ids).values_list('id', flat=True))
        if not gym_ids:
            return []
        qs = StaffProfile.objects.filter(active=True, role='gym_owner', gym_id__in=gym_ids)
        return list(qs.values_list('user_id', 'gym_id').distinct())

    @classmethod
    def _specific_staff(cls, audience):
        user_ids = list(audience.specific_staff.values_list('id', flat=True))
        if not user_ids:
            return []
        qs = StaffProfile.objects.filter(active=True, user_id__in=user_ids)
        qs = cls._narrow_to_gyms(qs, audience)
        return list(qs.values_list('user_id', 'gym_id').distinct())

    @classmethod
    def resolve(cls, communication: Communication) -> dict:
        try:
            audience = communication.audience
        except CommunicationAudience.DoesNotExist:
            logger.warning("AudienceResolver: communication=%s has no audience row configured", communication.id)
            return {'member_pairs': [], 'staff_pairs': []}

        Scope = CommunicationAudience.Scope
        scope = audience.scope
        member_pairs, staff_pairs = [], []

        if scope == Scope.EVERYONE or scope == Scope.SPECIFIC_GYM:
            member_pairs = cls._all_members(audience)
            staff_pairs = cls._all_staff(audience)
        elif scope == Scope.ALL_MEMBERS:
            member_pairs = cls._all_members(audience)
        elif scope == Scope.ACTIVE_MEMBERS:
            member_pairs = cls._members_filtered(audience, is_active=True)
        elif scope == Scope.EXPIRED_MEMBERS:
            member_pairs = cls._members_filtered(audience, due_lt=timezone.localdate())
        elif scope == Scope.EXPIRING_MEMBERS:
            today = timezone.localdate()
            member_pairs = cls._members_filtered(
                audience, due_gte=today, due_lte=today + timedelta(days=EXPIRING_SOON_WINDOW_DAYS),
            )
        elif scope == Scope.SPECIFIC_PLAN:
            member_pairs = cls._members_by_plan(audience)
        elif scope == Scope.SPECIFIC_CITY:
            member_pairs = cls._members_by_city(audience)
            staff_pairs = cls._staff_by_city(audience)
        elif scope in (Scope.SPECIFIC_STATE, Scope.SPECIFIC_COUNTRY):
            logger.warning(
                "AudienceResolver: scope=%s requested for communication=%s, but Gym has no "
                "state/country field yet — resolving to an empty audience. Add those fields "
                "to Gym.models.Gym to support this scope.",
                scope, communication.id,
            )
        elif scope == Scope.ALL_STAFF:
            staff_pairs = cls._all_staff(audience)
        elif scope == Scope.ALL_OWNERS:
            staff_pairs = cls._staff_by_role(audience, 'gym_owner')
        elif scope == Scope.ALL_RECEPTIONISTS:
            staff_pairs = cls._staff_by_role(audience, 'receptionist')
        elif scope == Scope.ALL_TRAINERS:
            staff_pairs = cls._staff_by_role(audience, 'trainer')
        elif scope == Scope.SUBSCRIPTION_PLAN:
            staff_pairs = cls._owners_by_subscription_plan(audience)
        elif scope == Scope.SPECIFIC_MEMBERS:
            member_pairs = cls._specific_members(audience)
        elif scope == Scope.SPECIFIC_STAFF:
            staff_pairs = cls._specific_staff(audience)
        else:
            logger.warning("AudienceResolver: unrecognized scope=%s for communication=%s", scope, communication.id)

        return {'member_pairs': member_pairs, 'staff_pairs': staff_pairs}

    @classmethod
    def user_matches(cls, communication: Communication, user, gym=None) -> bool:
        """
        True if `user` (optionally scoped to `gym`, e.g. request.gym set by
        GymMiddleware) falls inside this communication's configured
        audience. Used by the recipient-facing endpoints (communication
        center, popup, banner, unread count) — those need a per-user yes/no
        answer, not the bulk (user_id, gym_id) lists resolve() builds for
        dispatch fan-out.
        """
        if not user or not getattr(user, 'is_authenticated', False):
            return False

        try:
            audience = communication.audience
        except CommunicationAudience.DoesNotExist:
            return False

        Scope = CommunicationAudience.Scope
        scope = audience.scope
        gym_ids = set(audience.gyms.values_list('id', flat=True))

        def gym_ok(gym_id):
            return not gym_ids or gym_id in gym_ids

        if scope == Scope.EVERYONE:
            return True

        enrollment_qs = Enrollment.objects.filter(
            user=user, is_deleted=False,
        ).select_related('gym', 'selectPlan')
        if gym is not None:
            enrollment_qs = enrollment_qs.filter(gym=gym)
        enrollments = list(enrollment_qs)

        staff_profile = getattr(user, 'staff_profile', None)
        staff_in_scope = bool(
            staff_profile and staff_profile.active
            and (gym is None or staff_profile.gym_id == getattr(gym, 'id', gym))
            and gym_ok(staff_profile.gym_id)
        )

        if scope == Scope.ALL_MEMBERS:
            return any(gym_ok(e.gym_id) for e in enrollments)
        if scope == Scope.ACTIVE_MEMBERS:
            return any(gym_ok(e.gym_id) and e.is_active for e in enrollments)
        if scope == Scope.EXPIRED_MEMBERS:
            today = timezone.localdate()
            return any(gym_ok(e.gym_id) and e.DueDate and e.DueDate < today for e in enrollments)
        if scope == Scope.EXPIRING_MEMBERS:
            today = timezone.localdate()
            return any(
                gym_ok(e.gym_id) and e.DueDate and today <= e.DueDate <= today + timedelta(days=EXPIRING_SOON_WINDOW_DAYS)
                for e in enrollments
            )
        if scope == Scope.SPECIFIC_PLAN:
            plan_ids = set(audience.plans.values_list('id', flat=True))
            name = (audience.plan_name_filter or '').strip().lower()
            return any(
                gym_ok(e.gym_id) and (
                    e.selectPlan_id in plan_ids
                    or (name and e.selectPlan and e.selectPlan.plan.strip().lower() == name)
                )
                for e in enrollments
            )
        if scope == Scope.SPECIFIC_CITY:
            cities = {c.strip().lower() for c in (audience.cities or []) if str(c).strip()}
            member_hit = any(
                gym_ok(e.gym_id) and e.gym and e.gym.city and e.gym.city.strip().lower() in cities
                for e in enrollments
            )
            staff_hit = bool(
                staff_in_scope and staff_profile.gym and staff_profile.gym.city
                and staff_profile.gym.city.strip().lower() in cities
            )
            return member_hit or staff_hit
        if scope in (Scope.SPECIFIC_STATE, Scope.SPECIFIC_COUNTRY):
            return False  # not resolvable yet — see resolve()'s own note on this
        if scope == Scope.SPECIFIC_GYM:
            return any(gym_ok(e.gym_id) for e in enrollments) or staff_in_scope
        if scope == Scope.SPECIFIC_MEMBERS:
            return audience.specific_members.filter(pk=user.pk).exists()
        if scope == Scope.SPECIFIC_STAFF:
            return audience.specific_staff.filter(pk=user.pk).exists()
        if scope == Scope.ALL_STAFF:
            return staff_in_scope
        if scope == Scope.ALL_OWNERS:
            return staff_in_scope and staff_profile.role == 'gym_owner'
        if scope == Scope.ALL_RECEPTIONISTS:
            return staff_in_scope and staff_profile.role == 'receptionist'
        if scope == Scope.ALL_TRAINERS:
            return staff_in_scope and staff_profile.role == 'trainer'
        if scope == Scope.SUBSCRIPTION_PLAN:
            plan_ids = set(audience.subscription_plans.values_list('id', flat=True))
            return bool(
                staff_in_scope and staff_profile.role == 'gym_owner'
                and staff_profile.gym and staff_profile.gym.plan_id in plan_ids
            )

        logger.warning("AudienceResolver.user_matches: unrecognized scope=%s", scope)
        return False


class CommunicationDispatcher:
    """The Channel Resolver + fan-out. See module docstring."""

    CHANNEL_ID = 'entergym_communications'
    MAX_SEND_ATTEMPTS = 2  # bounded retry per batch/recipient — not infinite

    @classmethod
    def dispatch(cls, communication: Communication) -> dict:
        if communication.status == Communication.Status.CANCELLED:
            logger.info("CommunicationDispatcher: communication=%s is cancelled, skipping", communication.id)
            return {'success': 0, 'failure': 0, 'skipped': 'cancelled'}

        # Idempotency / concurrency guard: an atomic UPDATE...WHERE claim
        # so a double 'Publish Now' click or an overlapping cron tick can
        # never send the same communication twice. A single UPDATE is
        # already atomic at the DB level — no explicit transaction/lock
        # needed, and we deliberately don't hold one across the slow
        # network calls below.
        claimed = Communication.all_objects.filter(
            pk=communication.pk, is_dispatching=False, dispatched_at__isnull=True,
        ).update(is_dispatching=True)
        if not claimed:
            logger.info(
                "CommunicationDispatcher: communication=%s already dispatched or "
                "currently dispatching elsewhere — skipping duplicate send.",
                communication.id,
            )
            return {'success': 0, 'failure': 0, 'skipped': 'already_dispatched_or_in_progress'}

        try:
            return cls._do_dispatch(communication)
        finally:
            # Always release the guard, even on exception — a genuinely
            # failed run must be retryable on the next cron tick, not
            # permanently locked out.
            Communication.all_objects.filter(pk=communication.pk).update(is_dispatching=False)

    @classmethod
    def _do_dispatch(cls, communication: Communication) -> dict:
        recipients = AudienceResolver.resolve(communication)
        title = communication.title[:100]
        body = build_push_body(communication.description)
        deep_link = communication.get_deep_link()
        data = {
            "communication_id": str(communication.id),
            "type": communication.type,
            "priority": communication.priority,
            # "screen" mirrors the existing convention from Shop/AuthFit's own
            # payloads; falls back to the in-app Communication Center list
            # when no specific deep link is configured.
            "screen": deep_link["value"] if deep_link["kind"] == "screen" else "CommunicationDetail",
            "deep_link_kind": deep_link["kind"],
            "deep_link_value": deep_link["value"] or "",
        }

        success, failure = 0, 0

        if communication.channel_push:
            s, f = cls._dispatch_fcm(communication, recipients, title, body, data)
            success += s
            failure += f

        if communication.channel_web_push or communication.channel_pwa:
            s, f = cls._dispatch_web_push(communication, recipients, title, body)
            success += s
            failure += f

        if communication.channel_email or communication.channel_whatsapp or communication.channel_sms:
            logger.info(
                "CommunicationDispatcher: communication=%s requested a future channel "
                "(email=%s whatsapp=%s sms=%s) — no sender adapter exists yet, skipping.",
                communication.id, communication.channel_email,
                communication.channel_whatsapp, communication.channel_sms,
            )

        communication.mark_dispatched(success, failure)
        return {'success': success, 'failure': failure}

    @classmethod
    def _send_with_retry(cls, send_fn, *, description: str):
        """
        Runs `send_fn()` up to MAX_SEND_ATTEMPTS times. Bounded, synchronous
        retry for transient failures (e.g. a momentary network blip talking
        to FCM/the push service) — not a queue/backoff system, just enough
        to not lose a delivery to a one-off hiccup. Re-raises the last
        exception after all attempts are exhausted; the caller's existing
        try/except still isolates that from the rest of the batch.
        """
        last_exc = None
        for attempt in range(1, cls.MAX_SEND_ATTEMPTS + 1):
            try:
                return send_fn()
            except Exception as exc:
                last_exc = exc
                if attempt < cls.MAX_SEND_ATTEMPTS:
                    logger.warning(
                        "CommunicationDispatcher: %s attempt %d/%d failed, retrying: %s",
                        description, attempt, cls.MAX_SEND_ATTEMPTS, exc,
                    )
        raise last_exc

    @classmethod
    def _dispatch_fcm(cls, communication, recipients, title, body, data):
        success, failure = 0, 0

        for pairs, token_model in (
            (recipients['member_pairs'], UserDevice),
            (recipients['staff_pairs'], StaffDevice),
        ):
            if not pairs:
                continue
            by_gym = defaultdict(list)
            for user_id, gym_id in pairs:
                by_gym[gym_id].append(user_id)

            for gym_id, user_ids in by_gym.items():
                try:
                    tokens = list(
                        token_model.objects
                        .filter(gym_id=gym_id, user_id__in=user_ids, active=True)
                        .values_list('fcm_token', flat=True)
                    )
                    if not tokens:
                        continue
                    sent = cls._send_with_retry(
                        lambda: send_push_to_tokens(
                            tokens=tokens, title=title, body=body, data=data, channel_id=cls.CHANNEL_ID,
                        ),
                        description=f"FCM batch (gym={gym_id})",
                    )
                    success += sent
                    cls._log(communication, gym_id=gym_id, channel='fcm', ok=sent > 0)
                except Exception:
                    logger.exception(
                        "CommunicationDispatcher: FCM batch failed communication=%s gym=%s model=%s",
                        communication.id, gym_id, token_model.__name__,
                    )
                    failure += 1
                    cls._log(communication, gym_id=gym_id, channel='fcm', ok=False, error="FCM batch send raised an exception.")

        return success, failure

    @classmethod
    def _dispatch_web_push(cls, communication, recipients, title, body):
        success, failure = 0, 0
        url = communication.external_link or "/"

        all_pairs = recipients['member_pairs'] + recipients['staff_pairs']
        gym_by_user = {}
        for user_id, gym_id in all_pairs:
            gym_by_user.setdefault(user_id, gym_id)  # first gym wins for logging purposes

        if not gym_by_user:
            return 0, 0

        for user in User.objects.filter(id__in=gym_by_user.keys(), is_active=True):
            try:
                sent = cls._send_with_retry(
                    lambda: send_web_push(user=user, title=title, body=body, url=url),
                    description=f"web push (user={user.id})",
                )
                success += sent
                cls._log(
                    communication, gym_id=gym_by_user.get(user.id), recipient=user,
                    channel='web_push', ok=sent > 0,
                )
            except Exception:
                logger.exception(
                    "CommunicationDispatcher: web push failed communication=%s user=%s",
                    communication.id, user.id,
                )
                failure += 1
                cls._log(
                    communication, gym_id=gym_by_user.get(user.id), recipient=user,
                    channel='web_push', ok=False, error="Web push raised an exception.",
                )

        return success, failure

    @staticmethod
    def _log(communication, *, channel, ok, gym_id=None, recipient=None, error=""):
        try:
            CommunicationDeliveryLog.objects.create(
                communication=communication,
                gym_id=gym_id,
                recipient=recipient,
                channel=channel,
                status='sent' if ok else 'failed',
                error=error or ('' if ok else 'No successful deliveries for this batch.'),
            )
        except Exception:
            logger.exception(
                "CommunicationDispatcher: failed to write delivery log communication=%s channel=%s",
                communication.id, channel,
            )


def get_live_communications(channel_field=None):
    """Base queryset of currently-live communications, optionally narrowed
    to ones enabled for a given channel/display flag (e.g.
    'show_notification_center', 'show_popup', 'show_banner')."""
    now = timezone.now()
    qs = (
        Communication.objects
        .filter(is_active=True, status=Communication.Status.PUBLISHED, publish_at__lte=now)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .select_related('audience')
        .prefetch_related('audience__gyms')
    )
    if channel_field:
        qs = qs.filter(**{channel_field: True})
    return qs.order_by('-publish_at')


def get_visible_communications(user, gym=None, channel_field=None):
    """Live communications AudienceResolver.user_matches() says this
    specific user (optionally scoped to `gym`) should see. Shared by the
    Super Admin-independent surfaces: communication_center (web + API),
    the popup/banner home payload, and the unread-count/bell endpoints —
    one targeting implementation, not one per surface."""
    return [
        c for c in get_live_communications(channel_field)
        if hasattr(c, 'audience') and AudienceResolver.user_matches(c, user, gym=gym)
    ]


def get_delivery_status_map(user, communications):
    """{communication_id: {'read': bool, 'dismissed': bool}} from whatever
    delivery-log rows already exist for this user. A communication with no
    row yet (e.g. a batched FCM send nobody has opened) is simply absent —
    treat that as unread/not-dismissed."""
    ids = [c.id for c in communications]
    status_map = {}
    if not ids:
        return status_map
    for log in CommunicationDeliveryLog.objects.filter(recipient=user, communication_id__in=ids):
        entry = status_map.setdefault(log.communication_id, {'read': False, 'dismissed': False})
        if log.read_at:
            entry['read'] = True
        if log.dismissed_at:
            entry['dismissed'] = True
    return status_map


def dispatch_communication(communication: Communication) -> dict:
    """Thin module-level wrapper — mirrors announcements/utils.py's
    top-level `send_announcement_push(announcement)` calling convention,
    so callers (views/api/management command) don't need to know about
    CommunicationDispatcher as a class."""
    return CommunicationDispatcher.dispatch(communication)
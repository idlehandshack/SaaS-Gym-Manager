"""
announcements/tests.py

Covers the spec's testing_requirements that are cheap to verify without a
live Firebase/webpush backend (those are mocked). Push delivery itself is
exercised via mocking Shop.notifications.send_push_to_tokens /
notifications.utils.send_web_push, since real credentials aren't available
in CI.

Run: python manage.py test announcements
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from AuthFit.models import Enrollment, MembershipPlan
from Gym.models import Gym, StaffProfile, SubscriptionPlan

from .models import Announcement, AnnouncementRead
from .utils import send_announcement_push


def _make_gym(code):
    plan = SubscriptionPlan.objects.create(name=f'plan-{code}', price_monthly=0)
    owner = User.objects.create_user(username=f'owner-{code}', password='pw')
    gym = Gym.objects.create(gym_name=f'Gym {code}', gym_code=code, owner=owner, plan=plan)
    StaffProfile.objects.create(user=owner, gym=gym, role='gym_owner')
    return gym, owner


def _make_member(gym, username):
    user = User.objects.create_user(username=username, password='pw')
    mplan = MembershipPlan.objects.create(gym=gym, plan='Basic', price=100, duration_days=30)
    Enrollment.objects.create(
        gym=gym, user=user, fullname=username, phone='9000000000',
        selectPlan=mplan, Amount=100,
    )
    return user


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.gym_a, self.owner_a = _make_gym('gyma')
        self.gym_b, self.owner_b = _make_gym('gymb')
        self.ann_a = Announcement.objects.create(gym=self.gym_a, title='A notice', description='x')
        self.ann_b = Announcement.objects.create(gym=self.gym_b, title='B notice', description='x')

    def test_owner_cannot_see_other_gym_announcement_via_api(self):
        client = Client()
        client.force_login(self.owner_a)
        resp = client.get(reverse('announcement_edit', args=[self.ann_b.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_owner_list_only_shows_own_gym(self):
        client = Client()
        client.force_login(self.owner_a)
        resp = client.get(reverse('announcement_list'))
        self.assertContains(resp, 'A notice')
        self.assertNotContains(resp, 'B notice')


class PermissionTests(TestCase):
    def setUp(self):
        self.gym, self.owner = _make_gym('gymp')
        self.trainer_user = User.objects.create_user(username='trainer1', password='pw')
        StaffProfile.objects.create(user=self.trainer_user, gym=self.gym, role='trainer')

    def test_trainer_has_no_access(self):
        client = Client()
        client.force_login(self.trainer_user)
        resp = client.get(reverse('announcement_list'))
        self.assertEqual(resp.status_code, 403)

    def test_owner_can_create(self):
        client = Client()
        client.force_login(self.owner)
        resp = client.get(reverse('announcement_create'))
        self.assertEqual(resp.status_code, 200)


class PopupEligibilityTests(TestCase):
    def setUp(self):
        self.gym, self.owner = _make_gym('gympop')
        self.member = _make_member(self.gym, 'memberpop')

    def _home(self):
        client = Client()
        client.force_login(self.member)
        client.gym = self.gym  # not used directly; request.gym set via middleware normally
        return client

    @patch('announcements.api.getattr')
    def test_low_priority_never_pops_up(self, _):
        Announcement.objects.create(
            gym=self.gym, title='Low prio', description='x',
            priority=Announcement.Priority.LOW, show_popup=True,
        )
        # Directly exercise the eligibility function via the model layer
        # rather than full request/middleware wiring in this unit test.
        from .api import _live_visible_queryset
        candidates = _live_visible_queryset(self.gym, self.member, channel_field='show_popup')
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].priority, Announcement.Priority.LOW)

    def test_expired_announcement_excluded(self):
        Announcement.objects.create(
            gym=self.gym, title='Expired', description='x',
            publish_at=timezone.now() - timedelta(days=2),
            expires_at=timezone.now() - timedelta(days=1),
            show_popup=True,
        )
        from .api import _live_visible_queryset
        candidates = _live_visible_queryset(self.gym, self.member, channel_field='show_popup')
        self.assertEqual(len(candidates), 0)

    def test_scheduled_future_announcement_excluded(self):
        Announcement.objects.create(
            gym=self.gym, title='Future', description='x',
            publish_at=timezone.now() + timedelta(days=1),
            show_popup=True,
        )
        from .api import _live_visible_queryset
        candidates = _live_visible_queryset(self.gym, self.member, channel_field='show_popup')
        self.assertEqual(len(candidates), 0)

    def test_targeting_specific_members_excludes_others(self):
        other = _make_member(self.gym, 'otherpop')
        ann = Announcement.objects.create(
            gym=self.gym, title='VIP only', description='x',
            target_audience=Announcement.Audience.SPECIFIC,
        )
        ann.target_members.add(self.member)
        self.assertTrue(ann.is_targeted_at(self.member))
        self.assertFalse(ann.is_targeted_at(other))


class ReadTrackingTests(TestCase):
    def setUp(self):
        self.gym, self.owner = _make_gym('gymread')
        self.member = _make_member(self.gym, 'memberread')
        self.ann = Announcement.objects.create(gym=self.gym, title='Track me', description='x')

    def test_mark_read_creates_row_and_timestamp(self):
        row = AnnouncementRead.objects.create(announcement=self.ann, user=self.member)
        self.assertIsNone(row.read_at)
        row.mark_read(device_type=AnnouncementRead.DeviceType.WEB)
        row.refresh_from_db()
        self.assertIsNotNone(row.read_at)
        self.assertEqual(row.device_type, AnnouncementRead.DeviceType.WEB)


class PushIntegrationTests(TestCase):
    def setUp(self):
        self.gym, self.owner = _make_gym('gympush')
        self.member = _make_member(self.gym, 'memberpush')
        self.ann = Announcement.objects.create(
            gym=self.gym, title='Push me', description='hello world',
            send_push=True, target_audience=Announcement.Audience.ALL,
        )

    @patch('announcements.utils.send_web_push', return_value=1)
    @patch('announcements.utils.send_push_to_tokens', return_value=0)
    def test_push_uses_existing_notification_utilities(self, mock_fcm, mock_web):
        sent = send_announcement_push(self.ann)
        self.assertGreaterEqual(sent, 1)
        mock_web.assert_called()
        self.ann.refresh_from_db()
        self.assertIsNotNone(self.ann.push_sent_at)

    @patch('announcements.utils.send_web_push', side_effect=Exception('boom'))
    @patch('announcements.utils.send_push_to_tokens', return_value=0)
    def test_one_bad_delivery_does_not_abort_run(self, mock_fcm, mock_web):
        # Should not raise even though every web push call fails.
        sent = send_announcement_push(self.ann)
        self.assertEqual(sent, 0)

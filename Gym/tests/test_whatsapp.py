# Gym/tests/test_whatsapp.py
"""
Tests for the WhatsApp Cloud API integration (Steps 1-7).

Uses a fake WhatsAppTransport (no real network calls) injected via
whatsapp_service.get_transport, so these run offline and deterministically.

If this project uses pytest fixtures / factory_boy instead of plain
Django TestCase + setUp(), the fixture-building code in each setUp()
should move to fixtures — the assertions themselves don't change.
"""

from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.db import connection
from Gym.models import Gym, GymWhatsAppSettings, WhatsAppMessageLog, StaffProfile
from Gym.fields import get_fernet
from Gym.forms import WhatsAppSettingsForm
from Gym.services import whatsapp_service
from Gym.services.whatsapp_service import (
    WhatsAppTransportError, WhatsAppDisabled, WhatsAppNotConfigured,
    WhatsAppInvalidPhoneNumber, WhatsAppRateLimitExceeded,
)
from Gym.services.whatsapp_templates import (
    TEMPLATE_MEMBERSHIP_EXPIRY_BEFORE, TEMPLATE_MEMBERSHIP_EXPIRY_AFTER,
)
from datetime import time as dt_time, timedelta
from unittest import mock
from AuthFit.models import Enrollment, MembershipPlan
from AuthFit.notifications import _send_member_whatsapp_expiry, _reminder_time_matches
from django.utils import timezone as dj_timezone

TEST_FERNET_KEY = "zH1Vc2r3XowGyf0Bf7Rn8oNqz1t6f5uT1o1s8B1p9dY="  # test-only, not a real secret


def _make_gym(gym_code="testgym", gym_name="Test Gym"):
    owner = User.objects.create_user(username=f"owner_{gym_code}", password="x")
    return Gym.objects.create(gym_name=gym_name, gym_code=gym_code, owner=owner)


class FakeTransport:
    """
    Drop-in WhatsAppTransport double. `responses` is a list consumed
    in order by send_message(); `template_list` backs list_templates();
    `phone_metadata` backs get_phone_metadata(). Set `raise_error` to a
    WhatsAppTransportError instance to simulate a failure on the next call.
    """
    def __init__(self, phone_metadata=None, template_list=None):
        self.phone_metadata = phone_metadata or {"verified_name": "Test Gym"}
        self.template_list = template_list if template_list is not None else []
        self.sent_payloads = []
        self.raise_on_send = None
        self.raise_on_metadata = None
        self.raise_on_templates = None

    def send_message(self, payload):
        self.sent_payloads.append(payload)
        if self.raise_on_send:
            raise self.raise_on_send
        return {"messages": [{"id": "wamid.TEST123"}]}

    def get_phone_metadata(self):
        if self.raise_on_metadata:
            raise self.raise_on_metadata
        return dict(self.phone_metadata)

    def list_templates(self):
        if self.raise_on_templates:
            raise self.raise_on_templates
        return self.template_list


@override_settings(WHATSAPP_ENCRYPTION_KEY=TEST_FERNET_KEY)
class EncryptedFieldTests(TestCase):
    def setUp(self):
        get_fernet.cache_clear()  # settings changed between tests — invalidate the cached Fernet

    def test_round_trip_encrypt_decrypt(self):
        gym = _make_gym()
        settings_row = GymWhatsAppSettings.objects.create(
            gym=gym, permanent_access_token="super-secret-token-value",
        )
        settings_row.refresh_from_db()
        self.assertEqual(settings_row.permanent_access_token, "super-secret-token-value")

    def test_stored_value_is_not_plaintext_in_db(self):
        gym = _make_gym()

        GymWhatsAppSettings.objects.create(
            gym=gym,
            permanent_access_token="super-secret-token-value",
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT permanent_access_token "
                "FROM Gym_gymwhatsappsettings"
            )
            encrypted_value = cursor.fetchone()[0]

        self.assertNotIn(
            "super-secret-token-value",
            encrypted_value,
        )


class ModelBehaviorTests(TestCase):
    def setUp(self):
        self.gym = _make_gym()

    def test_phone_number_validator_rejects_non_e164(self):
        settings_row = GymWhatsAppSettings(gym=self.gym, phone_number="9876543210")
        with self.assertRaises(ValidationError):
            settings_row.full_clean()

    def test_phone_number_validator_accepts_e164(self):
        settings_row = GymWhatsAppSettings(gym=self.gym, phone_number="+919876543210")
        settings_row.full_clean(exclude=[f.name for f in GymWhatsAppSettings._meta.fields if f.name != 'phone_number'])

    def test_clean_requires_credentials_when_enabled(self):
        settings_row = GymWhatsAppSettings(gym=self.gym, enabled=True)
        with self.assertRaises(ValidationError):
            settings_row.clean()

    def test_mark_connected_clears_error_and_sets_status(self):
        settings_row = GymWhatsAppSettings.objects.create(gym=self.gym, status='error', last_error='boom')
        settings_row.mark_connected()
        settings_row.refresh_from_db()
        self.assertEqual(settings_row.status, 'connected')
        self.assertEqual(settings_row.last_error, '')
        self.assertIsNotNone(settings_row.verified_at)

    def test_mark_error_does_not_touch_enabled(self):
        settings_row = GymWhatsAppSettings.objects.create(gym=self.gym, enabled=True, status='connected')
        settings_row.mark_error("transient failure")
        settings_row.refresh_from_db()
        self.assertEqual(settings_row.status, 'error')
        self.assertTrue(settings_row.enabled)  # NOT flipped off

    def test_mark_disconnected_flips_enabled_off(self):
        settings_row = GymWhatsAppSettings.objects.create(gym=self.gym, enabled=True, status='connected')
        settings_row.mark_disconnected()
        settings_row.refresh_from_db()
        self.assertEqual(settings_row.status, 'disconnected')
        self.assertFalse(settings_row.enabled)

    def test_is_operational_only_checks_enabled(self):
        settings_row = GymWhatsAppSettings.objects.create(gym=self.gym, enabled=True, status='error')
        self.assertTrue(settings_row.is_operational)  # status='error' does NOT block

    def test_verify_webhook_signature_valid(self):
        import hashlib, hmac
        settings_row = GymWhatsAppSettings.objects.create(gym=self.gym, webhook_secret="whsec_test")
        body = b'{"event":"test"}'
        digest = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
        self.assertTrue(settings_row.verify_webhook_signature(body, f"sha256={digest}"))

    def test_verify_webhook_signature_invalid(self):
        settings_row = GymWhatsAppSettings.objects.create(gym=self.gym, webhook_secret="whsec_test")
        self.assertFalse(settings_row.verify_webhook_signature(b"{}", "sha256=deadbeef"))

    def test_dedup_constraint_blocks_second_row_same_key(self):
        WhatsAppMessageLog.objects.create(
            gym=self.gym, phone="+919876543210", message_type="template",
            deduplication_key="expiry:1:2026-08-01",
        )
        with self.assertRaises(Exception):
            WhatsAppMessageLog.objects.create(
                gym=self.gym, phone="+919876543210", message_type="template",
                deduplication_key="expiry:1:2026-08-01",
            )

    def test_dedup_constraint_allows_multiple_blank_keys(self):
        WhatsAppMessageLog.objects.create(gym=self.gym, phone="+911111111111", message_type="text")
        WhatsAppMessageLog.objects.create(gym=self.gym, phone="+912222222222", message_type="text")
        self.assertEqual(WhatsAppMessageLog.objects.filter(gym=self.gym).count(), 2)


@override_settings(WHATSAPP_ENCRYPTION_KEY=TEST_FERNET_KEY, WHATSAPP_DEFAULT_COUNTRY_CODE="+91")
class ServiceLayerTests(TestCase):
    def setUp(self):
        get_fernet.cache_clear()
        cache.clear()
        self.gym = _make_gym()
        self.settings_row = GymWhatsAppSettings.objects.create(
            gym=self.gym, enabled=True, status='connected',
            phone_number_id="123456", business_account_id="789012",
            permanent_access_token="token-abc",
        )

    def _patch_transport(self, fake):
        return mock.patch.object(whatsapp_service, "get_transport", return_value=fake)

    def test_send_template_requires_operational_settings(self):
        self.settings_row.enabled = False
        self.settings_row.save(update_fields=["enabled"])
        with self.assertRaises(WhatsAppDisabled):
            whatsapp_service.send_template(self.gym, "+919876543210", "some_template")

    def test_send_template_requires_configured_settings_when_no_row(self):
        other_gym = _make_gym(gym_code="othergym")
        with self.assertRaises(WhatsAppNotConfigured):
            whatsapp_service.send_template(other_gym, "+919876543210", "some_template")

    def test_multi_tenant_isolation_uses_correct_gym_credentials(self):
        """A second gym's own settings must never leak into the first gym's send."""
        other_gym = _make_gym(gym_code="othergym2")
        GymWhatsAppSettings.objects.create(
            gym=other_gym, enabled=True, status='connected',
            phone_number_id="999999", business_account_id="888888",
            permanent_access_token="other-token",
        )
        captured_tokens = []

        def fake_get_transport(settings_row):
            captured_tokens.append(settings_row.permanent_access_token)
            return FakeTransport()

        with mock.patch.object(whatsapp_service, "get_transport", side_effect=fake_get_transport):
            whatsapp_service.send_template(self.gym, "+919876543210", "t1")
            whatsapp_service.send_template(other_gym, "+919876543210", "t2")

        self.assertEqual(captured_tokens, ["token-abc", "other-token"])

    def test_invalid_phone_rejected_before_transport_call(self):
        fake = FakeTransport()
        with self._patch_transport(fake):
            with self.assertRaises(WhatsAppInvalidPhoneNumber):
                whatsapp_service.send_template(self.gym, "9876543210", "some_template")  # missing '+'
        self.assertEqual(fake.sent_payloads, [])  # never reached the transport

    def test_successful_send_marks_connected(self):
        self.settings_row.status = 'error'
        self.settings_row.save(update_fields=['status'])
        fake = FakeTransport()
        with self._patch_transport(fake):
            result = whatsapp_service.send_template(self.gym, "+919876543210", "some_template")
        self.assertTrue(result.success)
        self.settings_row.refresh_from_db()
        self.assertEqual(self.settings_row.status, 'connected')  # self-healed

    def test_failed_send_marks_error_but_not_disabled(self):
        fake = FakeTransport()
        fake.raise_on_send = WhatsAppTransportError("simulated 500", status_code=500)
        with self._patch_transport(fake):
            result = whatsapp_service.send_template(self.gym, "+919876543210", "some_template")
        self.assertFalse(result.success)
        self.settings_row.refresh_from_db()
        self.assertEqual(self.settings_row.status, 'error')
        self.assertTrue(self.settings_row.enabled)

    def test_idempotency_skips_duplicate_key_without_calling_transport(self):
        fake = FakeTransport()
        with self._patch_transport(fake):
            r1 = whatsapp_service.send_template(
                self.gym, "+919876543210", "some_template", deduplication_key="event:1"
            )
            r2 = whatsapp_service.send_template(
                self.gym, "+919876543210", "some_template", deduplication_key="event:1"
            )
        self.assertTrue(r1.success)
        self.assertFalse(r1.skipped_duplicate)
        self.assertTrue(r2.success)
        self.assertTrue(r2.skipped_duplicate)
        self.assertEqual(len(fake.sent_payloads), 1)  # second call never hit the transport
        self.assertEqual(
            WhatsAppMessageLog.objects.filter(gym=self.gym, deduplication_key="event:1").count(), 1
        )

    def test_idempotency_retries_after_a_failed_attempt(self):
        fake = FakeTransport()
        fake.raise_on_send = WhatsAppTransportError("simulated failure")
        with self._patch_transport(fake):
            r1 = whatsapp_service.send_template(
                self.gym, "+919876543210", "some_template", deduplication_key="event:2"
            )
        self.assertFalse(r1.success)

        fake2 = FakeTransport()  # this time it succeeds
        with self._patch_transport(fake2):
            r2 = whatsapp_service.send_template(
                self.gym, "+919876543210", "some_template", deduplication_key="event:2"
            )
        self.assertTrue(r2.success)
        self.assertFalse(r2.skipped_duplicate)  # NOT treated as a duplicate — prior attempt failed
        self.assertEqual(
            WhatsAppMessageLog.objects.filter(gym=self.gym, deduplication_key="event:2").count(), 1
        )  # same row updated, not a second row

    def test_phone_normalization_adds_default_country_code(self):
        self.assertEqual(whatsapp_service.normalize_phone_to_e164("9876543210"), "+919876543210")

    def test_phone_normalization_leaves_e164_untouched(self):
        self.assertEqual(whatsapp_service.normalize_phone_to_e164("+14155552671"), "+14155552671")

    def test_test_message_rate_limit(self):
        fake = FakeTransport()
        with self._patch_transport(fake):
            for _ in range(5):
                whatsapp_service.send_test_message(self.gym, "+919876543210")
            with self.assertRaises(WhatsAppRateLimitExceeded):
                whatsapp_service.send_test_message(self.gym, "+919876543210")

    def test_verify_connection_success_with_required_templates(self):
        fake = FakeTransport(template_list=[
            {"name": TEMPLATE_MEMBERSHIP_EXPIRY_BEFORE, "status": "APPROVED"},
            {"name": TEMPLATE_MEMBERSHIP_EXPIRY_AFTER, "status": "APPROVED"},
        ])
        with self._patch_transport(fake):
            result = whatsapp_service.verify_connection(self.gym)
        self.assertTrue(result.success)
        self.assertTrue(result.response["ready"])
        self.settings_row.refresh_from_db()
        self.assertEqual(self.settings_row.status, 'connected')

    def test_verify_connection_fails_when_template_missing(self):
        fake = FakeTransport(template_list=[
            {"name": TEMPLATE_MEMBERSHIP_EXPIRY_BEFORE, "status": "APPROVED"},
            # membership_expiry_after missing entirely
        ])
        with self._patch_transport(fake):
            result = whatsapp_service.verify_connection(self.gym)
        self.assertFalse(result.success)
        self.assertIn(TEMPLATE_MEMBERSHIP_EXPIRY_AFTER, result.response["missing_templates"])
        self.settings_row.refresh_from_db()
        self.assertEqual(self.settings_row.status, 'error')

    def test_verify_connection_fails_when_template_pending_not_approved(self):
        fake = FakeTransport(template_list=[
            {"name": TEMPLATE_MEMBERSHIP_EXPIRY_BEFORE, "status": "PENDING"},  # not APPROVED
            {"name": TEMPLATE_MEMBERSHIP_EXPIRY_AFTER, "status": "APPROVED"},
        ])
        with self._patch_transport(fake):
            result = whatsapp_service.verify_connection(self.gym)
        self.assertFalse(result.success)
        self.assertIn(TEMPLATE_MEMBERSHIP_EXPIRY_BEFORE, result.response["missing_templates"])

    def test_verify_connection_phone_failure_short_circuits_before_templates(self):
        fake = FakeTransport()
        fake.raise_on_metadata = WhatsAppTransportError("invalid token", status_code=401)
        with self._patch_transport(fake):
            result = whatsapp_service.verify_connection(self.gym)
        self.assertFalse(result.success)
        self.assertFalse(result.response["business_account_reachable"])


@override_settings(ENCRYPTION_KEY=TEST_FERNET_KEY)
class WhatsAppSettingsFormTests(TestCase):
    def setUp(self):
        get_fernet.cache_clear()
        self.gym = _make_gym()

    def test_rejects_non_numeric_phone_number_id(self):
        form = WhatsAppSettingsForm(data={
            "business_name": "Test", "phone_number": "+919876543210",
            "phone_number_id": "abc123", "business_account_id": "123456",
            "permanent_access_token": "tok", "webhook_verify_token": "", "webhook_secret": "","reminder_days_before": 3,
            "reminder_time": "14:00:00",
            "timezone": "Asia/Kolkata",
            "send_post_expiry_reminder": True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("phone_number_id", form.errors)

    def test_rejects_phone_without_plus(self):
        form = WhatsAppSettingsForm(data={
            "business_name": "Test", "phone_number": "919876543210",
            "phone_number_id": "123456", "business_account_id": "123456",
            "permanent_access_token": "tok", "webhook_verify_token": "", "webhook_secret": "",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("phone_number", form.errors)

    def test_blank_secret_on_edit_preserves_existing_value(self):
        settings_row = GymWhatsAppSettings.objects.create(
            gym=self.gym, permanent_access_token="original-secret-token",
        )
        form = WhatsAppSettingsForm(
            data={
                "business_name": "Test",
                "phone_number": "+919876543210",
                "phone_number_id": "123456",
                "business_account_id": "123456",
                "permanent_access_token": "",
                "webhook_verify_token": "",
                "webhook_secret": "",

                "reminder_days_before": 3,
                "reminder_time": "14:00:00",
                "timezone": "Asia/Kolkata",
                "send_post_expiry_reminder": True,
            },
            instance=settings_row,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        saved.refresh_from_db()
        self.assertEqual(saved.permanent_access_token, "original-secret-token")


@override_settings(WHATSAPP_ENCRYPTION_KEY=TEST_FERNET_KEY)
class WhatsAppViewPermissionTests(TestCase):
    """
    Assumes GymMiddleware resolves request.gym / request.staff_role from
    the authenticated user's StaffProfile, per Gym/middleware.py. Uses
    DEV_GYM_CODE-style dev resolution is NOT relied on here — instead
    logs in as a StaffProfile-linked user and trusts the middleware.
    If your test settings don't route through GymMiddleware the same
    way local dev does, these may need a request-factory-based approach
    instead of self.client — flag it if so.
    """
    def setUp(self):
        get_fernet.cache_clear()
        self.gym = _make_gym()
        GymWhatsAppSettings.objects.create(gym=self.gym, enabled=True, status='connected', phone_number="+919876543210", business_name="Test Gym")

        self.owner_user = self.gym.owner
        self.owner_user.set_password("pass")
        self.owner_user.save()
        StaffProfile.objects.filter(user=self.owner_user).delete()
        StaffProfile.objects.create(user=self.owner_user, gym=self.gym, role='gym_owner', active=True)

        self.receptionist_user = User.objects.create_user(username="reception1", password="pass")
        StaffProfile.objects.create(user=self.receptionist_user, gym=self.gym, role='receptionist', active=True)

    def test_receptionist_cannot_access_settings_page(self):
        self.client.login(username="reception1", password="pass")
        response = self.client.get(reverse('whatsapp_settings'), HTTP_HOST=f"{self.gym.gym_code}.entergym.in")
        self.assertEqual(response.status_code, 403)

    def test_owner_can_access_settings_page(self):
        self.client.login(username=self.owner_user.username, password="pass")
        response = self.client.get(reverse('whatsapp_settings'), HTTP_HOST=f"{self.gym.gym_code}.entergym.in")
        self.assertEqual(response.status_code, 200)

    def test_status_endpoint_masks_phone_number(self):
        self.client.login(username=self.owner_user.username, password="pass")
        response = self.client.get(reverse('whatsapp_status'), HTTP_HOST=f"{self.gym.gym_code}.entergym.in")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertNotEqual(data["phone_number"], "+919876543210")
        self.assertTrue(data["phone_number"].startswith("+91"))
        self.assertTrue(data["phone_number"].endswith("3210"))
        self.assertIn("*", data["phone_number"])

    def test_status_endpoint_ready_flag_true_when_connected_and_enabled(self):
        self.client.login(username=self.owner_user.username, password="pass")
        response = self.client.get(reverse('whatsapp_status'), HTTP_HOST=f"{self.gym.gym_code}.entergym.in")
        self.assertTrue(response.json()["ready"])

    def test_status_endpoint_ready_flag_false_when_status_error(self):
        wa = self.gym.whatsapp_settings
        wa.status = 'error'
        wa.save(update_fields=['status'])
        self.client.login(username=self.owner_user.username, password="pass")
        response = self.client.get(reverse('whatsapp_status'), HTTP_HOST=f"{self.gym.gym_code}.entergym.in")
        self.assertFalse(response.json()["ready"])

@override_settings(WHATSAPP_ENCRYPTION_KEY=TEST_FERNET_KEY)
class ConfigurableReminderTests(TestCase):
    def setUp(self):
        get_fernet.cache_clear()
        cache.clear()
        self.gym = _make_gym()
        self.wa = GymWhatsAppSettings.objects.create(
            gym=self.gym, enabled=True, status='connected',
            phone_number_id="123456", business_account_id="789012",
            permanent_access_token="token-abc",
            reminder_days_before=3, reminder_time=dt_time(14, 0),
            send_post_expiry_reminder=True, timezone="Asia/Kolkata",
        )
        self.plan = MembershipPlan.objects.create(gym=self.gym, plan="Basic", price=1000, duration_days=30)
 
    def _make_enrollment(self, due_offset_days):
        phone = f"9876543{Enrollment.objects.count():03d}"

        return Enrollment.objects.create(
            gym=self.gym,
            fullname="Test Member",
            phone=phone,
            selectPlan=self.plan,
            Amount=1000,
            DueDate=dj_timezone.localdate() + timedelta(days=due_offset_days),
        )
 
    def _patch_transport(self, fake):
        return mock.patch.object(whatsapp_service, "get_transport", return_value=fake)
 
    def _fixed_time_at(self, hour, minute, tz="Asia/Kolkata"):
        from zoneinfo import ZoneInfo
        now_local = dj_timezone.now().astimezone(ZoneInfo(tz)).replace(hour=hour, minute=minute)
        return now_local.astimezone(ZoneInfo("UTC"))
 
    def test_reminder_time_matches_within_tolerance(self):
        now = self._fixed_time_at(14, 10)  # 10 min after configured 14:00
        self.assertTrue(_reminder_time_matches(self.wa, now_utc=now))
 
    def test_reminder_time_does_not_match_outside_tolerance(self):
        now = self._fixed_time_at(9, 0)  # configured for 14:00, this is 09:00
        self.assertFalse(_reminder_time_matches(self.wa, now_utc=now))
 
    def test_fires_only_on_configured_day_offset(self):
        enr_day5 = self._make_enrollment(due_offset_days=5)
        enr_day3 = self._make_enrollment(due_offset_days=3)  # not configured (gym wants 5)
        now = self._fixed_time_at(14, 0)
 
        fake = FakeTransport()
        with self._patch_transport(fake), mock.patch("AuthFit.notifications.timezone.now", return_value=now):
            result_5 = _send_member_whatsapp_expiry(enr_day5, days_left=5, gym_code=self.gym.gym_code)
            result_3 = _send_member_whatsapp_expiry(enr_day3, days_left=3, gym_code=self.gym.gym_code)
 
        self.assertFalse(result_5)
        self.assertTrue(result_3)
 
    def test_does_not_fire_outside_configured_time_window(self):
        enr = self._make_enrollment(due_offset_days=5)
        now = self._fixed_time_at(9, 0)  # gym configured for 14:00
 
        fake = FakeTransport()
        with self._patch_transport(fake), mock.patch("AuthFit.notifications.timezone.now", return_value=now):
            result = _send_member_whatsapp_expiry(enr, days_left=5, gym_code=self.gym.gym_code)
 
        self.assertFalse(result)
        self.assertEqual(fake.sent_payloads, [])
 
    def test_post_expiry_fires_only_on_day_minus_one(self):
        enr_minus1 = self._make_enrollment(due_offset_days=-1)
        enr_minus2 = self._make_enrollment(due_offset_days=-2)
        now = self._fixed_time_at(14, 0)
 
        fake = FakeTransport()
        with self._patch_transport(fake), mock.patch("AuthFit.notifications.timezone.now", return_value=now):
            result_m1 = _send_member_whatsapp_expiry(enr_minus1, days_left=-1, gym_code=self.gym.gym_code)
            result_m2 = _send_member_whatsapp_expiry(enr_minus2, days_left=-2, gym_code=self.gym.gym_code)
 
        self.assertTrue(result_m1)
        self.assertFalse(result_m2)  # never fires on day -2 or earlier
 
    def test_post_expiry_disabled_via_setting(self):
        self.wa.send_post_expiry_reminder = False
        self.wa.save(update_fields=["send_post_expiry_reminder"])
        enr = self._make_enrollment(due_offset_days=-1)
        now = self._fixed_time_at(14, 0)
 
        fake = FakeTransport()
        with self._patch_transport(fake), mock.patch("AuthFit.notifications.timezone.now", return_value=now):
            result = _send_member_whatsapp_expiry(enr, days_left=-1, gym_code=self.gym.gym_code)
 
        self.assertFalse(result)
        self.assertEqual(fake.sent_payloads, [])
 
    def test_duplicate_prevention_still_works_across_multiple_cron_runs(self):
        """
        Simulates the SAME day's reminder_time window being hit by two
        separate cron invocations (e.g. a retry, or overlapping runs) —
        WhatsApp's own deduplication_key must still block the second send,
        exactly as before this feature.
        """
        enr = self._make_enrollment(due_offset_days=3)
        now = self._fixed_time_at(14, 0)
 
        fake = FakeTransport()
        with self._patch_transport(fake), mock.patch("AuthFit.notifications.timezone.now", return_value=now):
            r1 = _send_member_whatsapp_expiry(enr, days_left=3, gym_code=self.gym.gym_code)
            r2 = _send_member_whatsapp_expiry(enr, days_left=3, gym_code=self.gym.gym_code)
 
        self.assertTrue(r1)
        self.assertTrue(r2)
        self.assertEqual(len(fake.sent_payloads), 1)  # second call never reached the transport
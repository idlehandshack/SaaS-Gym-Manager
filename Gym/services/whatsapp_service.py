# Gym/services/whatsapp_service.py  (v2 — production-hardening pass)
"""
WhatsApp Cloud API service layer for EnterGYM.

Multi-tenant rule (non-negotiable): every function takes a `gym` and
resolves that gym's OWN GymWhatsAppSettings row. No global/shared
credential anywhere in this file.

Architecture (v2):

    whatsapp_service.py         — business logic: gym resolution, phone
                                   validation, idempotency, rate limiting,
                                   logging, orchestration
            |
    WhatsAppTransport (ABC)     — send_message(payload) / get_phone_metadata()
            |
    MetaCloudTransport          — today's only implementation (requests-based
                                   Meta Graph API client, with the retry
                                   policy that used to live directly in this
                                   module)

Adding a future provider (Twilio, Gupshup, Interakt, ...) means writing a
new WhatsAppTransport subclass and adding one branch to get_transport()
— send_text/send_template/send_bulk_messages and every notification
caller in Step 4 are completely unaware of which transport is in use.

Idempotency (point 2): pass `deduplication_key=` to any send_* function
and the service guarantees at-most-one successful send for that key,
using the DB's partial UniqueConstraint on WhatsAppMessageLog as the
actual race-free guard — not a read-then-write check.

Bulk-sending abstraction (Celery-ready without Celery) is unchanged from
Step 3 — see send_bulk_messages()'s docstring.
"""

from __future__ import annotations

import abc
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction

from Gym.models import Gym, GymWhatsAppSettings, WhatsAppMessageLog
from Gym.services.whatsapp_templates import REQUIRED_TEMPLATES
logger = logging.getLogger(__name__)
from Gym.services.whatsapp_templates import TEMPLATE_MEMBERSHIP_EXPIRY_BEFORE
# Point 1 — configurable, read at call time (not frozen at import time,
# so tests / settings overrides take effect without reloading this module).
def _api_version() -> str:
    return getattr(settings, "WHATSAPP_API_VERSION", "v23.0")


META_API_BASE = "https://graph.facebook.com"

_MAX_HTTP_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.5
_BULK_SEND_DELAY_SECONDS = 0.3

# Point 3 — E.164, same pattern as the model validator (kept in sync
# manually since this is a plain function check, not a model field;
# see the note in models_v2_changes.py's E164_PHONE_VALIDATOR).
_E164_RE = re.compile(r'^\+[1-9]\d{7,14}$')

# Point 6 — test-message rate limit.
_TEST_MESSAGE_RATE_LIMIT = 5
_TEST_MESSAGE_RATE_WINDOW_SECONDS = 60

# Point 7 — response trimming: only these top-level keys are ever
# persisted from Meta's response body.
_RESPONSE_FIELDS_TO_KEEP = ("messaging_product", "contacts", "messages", "error")


# ── Exceptions ────────────────────────────────────────────────────────────────

class WhatsAppError(Exception):
    """Base class for all WhatsApp service errors."""


class WhatsAppNotConfigured(WhatsAppError):
    pass


class WhatsAppDisabled(WhatsAppError):
    pass


class WhatsAppInvalidPhoneNumber(WhatsAppError):
    pass


class WhatsAppRateLimitExceeded(WhatsAppError):
    pass


class WhatsAppTransportError(WhatsAppError):
    """Raised by a WhatsAppTransport implementation on any API-level failure."""

    def __init__(self, message: str, response_body: Optional[dict] = None, status_code: Optional[int] = None):
        super().__init__(message)
        self.response_body = response_body or {}
        self.status_code = status_code


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class WhatsAppSendResult:
    success: bool
    message_id: str = ""
    response: dict = field(default_factory=dict)
    error: str = ""
    status_code: Optional[int] = None
    skipped_duplicate: bool = False
    log: Optional[WhatsAppMessageLog] = None


@dataclass
class BulkSendSummary:
    total: int
    sent: int
    failed: int
    skipped_duplicates: int = 0
    results: list = field(default_factory=list)


# ── Transport abstraction (point 4) ────────────────────────────────────────────

class WhatsAppTransport(abc.ABC):
    @abc.abstractmethod
    def send_message(self, payload: dict) -> dict:
        """POST a message payload. Returns the parsed response body on
        success. Raises WhatsAppTransportError on any failure."""
 
    @abc.abstractmethod
    def get_phone_metadata(self) -> dict:
        """Fetch metadata about the connected phone number."""
 
    @abc.abstractmethod
    def list_templates(self) -> list[dict]:
        """
        Return every message template registered on this connection's
        Business Account, each as {"name": str, "status": str}. Status
        values from Meta include 'APPROVED', 'PENDING', 'REJECTED' —
        callers (verify_connection) must filter to 'APPROVED' themselves;
        this method returns the raw list unfiltered.
        Raises WhatsAppTransportError on failure.
        """

class MetaCloudTransport(WhatsAppTransport):
    """
    Default (and currently only) transport: Meta's WhatsApp Cloud API via
    plain HTTPS + `requests`. Owns the retry policy for transient
    failures — this is exactly the logic that lived in this module's
    `_post_to_meta` in Step 3, moved here unchanged in behavior.
    """

    def __init__(self, settings_row: GymWhatsAppSettings):
        self.settings_row = settings_row

    @property
    def _base_url(self) -> str:
        return f"{META_API_BASE}/{_api_version()}/{self.settings_row.phone_number_id}"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.settings_row.permanent_access_token}",
            "Content-Type": "application/json",
        }
    def send_message(self, payload: dict) -> dict:
        return self._post(f"{self._base_url}/messages", payload)

    def get_phone_metadata(self) -> dict:
        return self._get(self._base_url, params={"fields": "verified_name,display_phone_number,quality_rating"})
    @property
    def _business_account_url(self) -> str:
        """
        Templates live under the Business Account, not the phone number
        — a different Graph API sub-resource than _base_url (which is
        keyed off phone_number_id for /messages and phone metadata).
        """
        return f"{META_API_BASE}/{_api_version()}/{self.settings_row.business_account_id}"
 
    def list_templates(self) -> list[dict]:
        """
        GET /{business_account_id}/message_templates?fields=name,status&limit=100
 
        Single page only (limit=100) — a gym with more than 100 templates
        would need pagination via the response's `paging.cursors.after`,
        not implemented here since it's an extreme edge case for a single
        gym's own WABA; flagged as a known simplification, not silently
        handled.
        """
        body = self._get(
            f"{self._business_account_url}/message_templates",
            params={"fields": "name,status", "limit": 100},
        )
        return [
            {"name": t.get("name", ""), "status": t.get("status", "")}
            for t in body.get("data", [])
        ]
    def _post(self, url: str, payload: dict) -> dict:
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_HTTP_RETRIES + 1):
            try:
                response = requests.post(url, headers=self._headers(), json=payload, timeout=15)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < _MAX_HTTP_RETRIES:
                    logger.warning("WhatsApp transport network error (attempt %d/%d): %s",
                                   attempt + 1, _MAX_HTTP_RETRIES + 1, exc)
                    time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise WhatsAppTransportError(f"Network error contacting WhatsApp API: {exc}") from exc

            body = self._safe_json(response)
            if response.status_code >= 500:
                if attempt < _MAX_HTTP_RETRIES:
                    logger.warning("WhatsApp transport 5xx (attempt %d/%d)", attempt + 1, _MAX_HTTP_RETRIES + 1)
                    time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise WhatsAppTransportError(
                    f"WhatsApp API returned {response.status_code}", response_body=body,
                    status_code=response.status_code,
                )

            if response.status_code >= 400 or "error" in body:
                error_detail = body.get("error", {}).get("message", response.text[:300])
                raise WhatsAppTransportError(
                    f"WhatsApp API error: {error_detail}", response_body=body,
                    status_code=response.status_code,
                )
            return {**body, "_status_code": response.status_code}

        raise WhatsAppTransportError(f"WhatsApp API call failed after retries: {last_exc}")

    def _get(self, url: str, params: dict) -> dict:
        try:
            response = requests.get(url, headers=self._headers(), params=params, timeout=15)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise WhatsAppTransportError(f"Network error contacting WhatsApp API: {exc}") from exc

        body = self._safe_json(response)
        if response.status_code >= 400 or "error" in body:
            error_detail = body.get("error", {}).get("message", response.text[:300])
            raise WhatsAppTransportError(
                f"WhatsApp API error: {error_detail}", response_body=body,
                status_code=response.status_code,
            )
        return {**body, "_status_code": response.status_code}

    @staticmethod
    def _safe_json(response: requests.Response) -> dict:
        try:
            return response.json()
        except ValueError:
            return {}


def get_transport(settings_row: GymWhatsAppSettings) -> WhatsAppTransport:
    """
    Transport factory — the ONE place that decides which provider a gym
    uses. Today always MetaCloudTransport. A future per-gym provider
    choice (e.g. `settings_row.provider == 'twilio'`) would branch here
    only — nothing else in this file changes.
    """
    return MetaCloudTransport(settings_row)


# ── Credential resolution ──────────────────────────────────────────────────────

def _get_operational_settings(gym: Gym) -> GymWhatsAppSettings:
    """
    Point 5: only `enabled` (via is_operational, which is now just
    `enabled`) gates sending — a status of 'error' from a prior transient
    failure does NOT block this. Credential completeness is still
    enforced explicitly below, independent of `status`.
    """
    try:
        settings_row = gym.whatsapp_settings
    except GymWhatsAppSettings.DoesNotExist:
        raise WhatsAppNotConfigured(f"Gym '{gym.gym_code}' has no WhatsApp settings configured.")

    if not settings_row.is_operational:
        raise WhatsAppDisabled(f"WhatsApp is disabled for gym '{gym.gym_code}'.")

    if not (settings_row.phone_number_id and settings_row.permanent_access_token):
        raise WhatsAppNotConfigured(
            f"Gym '{gym.gym_code}' is enabled but missing Phone Number ID or Access Token."
        )
    return settings_row


def _get_configured_settings(gym: Gym) -> GymWhatsAppSettings:
    """Used by verify_connection()/send_test_message(), which must work
    even before `enabled` is flipped on."""
    try:
        settings_row = gym.whatsapp_settings
    except GymWhatsAppSettings.DoesNotExist:
        raise WhatsAppNotConfigured(f"Gym '{gym.gym_code}' has no WhatsApp settings configured.")

    if not (settings_row.phone_number_id and settings_row.permanent_access_token):
        raise WhatsAppNotConfigured(f"Gym '{gym.gym_code}' is missing Phone Number ID or Access Token.")
    return settings_row


def _validate_phone(to_phone: str) -> None:
    """Point 3 — defense in depth. The model-level RegexValidator only
    protects data entered through a ModelForm; anything calling send_*
    programmatically (e.g. Step 4's notification triggers, built from
    `enrollment.phone` which may not be E.164-formatted in the DB) must
    be checked here too, before any API request is built."""
    if not _E164_RE.match(to_phone or ""):
        raise WhatsAppInvalidPhoneNumber(
            f"'{to_phone}' is not a valid E.164 phone number (expected e.g. +919876543210)."
        )


# ── Idempotency (point 2) ──────────────────────────────────────────────────────

def _reserve_or_get_existing_log(
    *, gym: Gym, member, phone: str, message_type: str, template_name: str,
    deduplication_key: str,
) -> tuple[WhatsAppMessageLog, bool]:
    """
    Atomically reserves a log row for `deduplication_key`, or returns the
    existing one. Returns (log, created).

    Race-free by construction: relies on the partial UniqueConstraint
    ('gym', 'deduplication_key') WHERE deduplication_key != '' — the
    database itself is the arbiter, not a preceding SELECT. Two
    concurrent callers with the same key will have exactly one INSERT
    succeed; the loser catches IntegrityError and re-fetches.
    """
    try:
        with transaction.atomic():
            log = WhatsAppMessageLog.objects.create(
                gym=gym, member=member, phone=phone, message_type=message_type,
                template_name=template_name, deduplication_key=deduplication_key,
                status='queued',
            )
        return log, True
    except IntegrityError:
        existing = WhatsAppMessageLog.objects.get(gym=gym, deduplication_key=deduplication_key)
        return existing, False


# ── Response trimming & logging (point 7) ──────────────────────────────────────

def _trim_response(response_body: dict) -> dict:
    return {k: response_body[k] for k in _RESPONSE_FIELDS_TO_KEEP if k in response_body}


def _extract_message_id(response_body: dict) -> str:
    try:
        return response_body["messages"][0]["id"]
    except (KeyError, IndexError, TypeError):
        return ""


def _finalize_log(log: WhatsAppMessageLog, result: WhatsAppSendResult) -> None:
    log.status = 'sent' if result.success else 'failed'
    log.message_id = result.message_id
    log.response = _trim_response(result.response)
    log.error = result.error[:2000]
    log.status_code = result.status_code
    log.save(update_fields=['status', 'message_id', 'response', 'error', 'status_code'])
    result.log = log


# ── Core send orchestration ────────────────────────────────────────────────────

def _send(
    gym: Gym, settings_row: GymWhatsAppSettings, payload: dict, to_phone: str,
    message_type: str, template_name: str, member, deduplication_key: str,
) -> WhatsAppSendResult:
    _validate_phone(to_phone)

    log = None
    if deduplication_key:
        log, created = _reserve_or_get_existing_log(
            gym=gym, member=member, phone=to_phone, message_type=message_type,
            template_name=template_name, deduplication_key=deduplication_key,
        )
        if not created and log.status == 'sent':
            logger.info(
                "WhatsApp send skipped — duplicate deduplication_key=%s gym=%s (already sent, message_id=%s)",
                deduplication_key, gym.gym_code, log.message_id,
            )
            return WhatsAppSendResult(
                success=True, message_id=log.message_id, skipped_duplicate=True, log=log,
            )
        # not created and status in ('queued','failed') -> a genuine earlier
        # attempt that never succeeded; fall through and retry, reusing `log`.

    transport = get_transport(settings_row)
    try:
        body = transport.send_message(payload)
        status_code = body.pop("_status_code", None)
        result = WhatsAppSendResult(
            success=True, message_id=_extract_message_id(body), response=body, status_code=status_code,
        )
        settings_row.mark_connected()
        logger.info("WhatsApp %s sent gym=%s to=%s message_id=%s",
                    message_type, gym.gym_code, to_phone, result.message_id)
    except WhatsAppTransportError as exc:
        result = WhatsAppSendResult(
            success=False, error=str(exc), response=exc.response_body, status_code=exc.status_code,
        )
        settings_row.mark_error(str(exc))
        logger.warning("WhatsApp %s failed gym=%s to=%s error=%s",
                       message_type, gym.gym_code, to_phone, exc)

    if log is not None:
        _finalize_log(log, result)
    else:
        # No dedup key supplied (ad-hoc/test send) — still log for audit,
        # just without reservation semantics.
        log = WhatsAppMessageLog.objects.create(
            gym=gym, member=member, phone=to_phone, message_type=message_type,
            template_name=template_name, deduplication_key='',
            status='sent' if result.success else 'failed',
            message_id=result.message_id, response=_trim_response(result.response),
            error=result.error, status_code=result.status_code,
        )
        result.log = log

    return result


# ── Single-message senders ─────────────────────────────────────────────────────

def send_text(gym: Gym, to_phone: str, body: str, *, member=None, deduplication_key: str = "") -> WhatsAppSendResult:
    settings_row = _get_operational_settings(gym)
    payload = {"messaging_product": "whatsapp", "to": to_phone, "type": "text", "text": {"body": body}}
    return _send(gym, settings_row, payload, to_phone, "text", "", member, deduplication_key)


def send_template(
    gym: Gym, to_phone: str, template_name: str, *,
    language_code: str = "en", components: Optional[list] = None, member=None,
    deduplication_key: str = "",
) -> WhatsAppSendResult:
    settings_row = _get_operational_settings(gym)
    payload = {
        "messaging_product": "whatsapp", "to": to_phone, "type": "template",
        "template": {"name": template_name, "language": {"code": language_code}},
    }
    if components:
        payload["template"]["components"] = components
    return _send(gym, settings_row, payload, to_phone, "template", template_name, member, deduplication_key)


def send_image(gym: Gym, to_phone: str, image_url: str, *, caption: str = "", member=None, deduplication_key: str = "") -> WhatsAppSendResult:
    settings_row = _get_operational_settings(gym)
    payload = {
        "messaging_product": "whatsapp", "to": to_phone, "type": "image",
        "image": {"link": image_url, "caption": caption} if caption else {"link": image_url},
    }
    return _send(gym, settings_row, payload, to_phone, "image", "", member, deduplication_key)


def send_document(gym: Gym, to_phone: str, document_url: str, *, filename: str = "", caption: str = "", member=None, deduplication_key: str = "") -> WhatsAppSendResult:
    settings_row = _get_operational_settings(gym)
    doc = {"link": document_url}
    if filename:
        doc["filename"] = filename
    if caption:
        doc["caption"] = caption
    payload = {"messaging_product": "whatsapp", "to": to_phone, "type": "document", "document": doc}
    return _send(gym, settings_row, payload, to_phone, "document", "", member, deduplication_key)


def send_location(gym: Gym, to_phone: str, *, latitude: float, longitude: float, name: str = "", address: str = "", member=None, deduplication_key: str = "") -> WhatsAppSendResult:
    settings_row = _get_operational_settings(gym)
    payload = {
        "messaging_product": "whatsapp", "to": to_phone, "type": "location",
        "location": {"latitude": latitude, "longitude": longitude, "name": name, "address": address},
    }
    return _send(gym, settings_row, payload, to_phone, "location", "", member, deduplication_key)


def send_interactive_buttons(
    gym: Gym, to_phone: str, *, body_text: str, buttons: list[dict], member=None, deduplication_key: str = "",
) -> WhatsAppSendResult:
    settings_row = _get_operational_settings(gym)
    payload = {
        "messaging_product": "whatsapp", "to": to_phone, "type": "interactive",
        "interactive": {
            "type": "button", "body": {"text": body_text},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b["id"], "title": b["title"]}} for b in buttons[:3]]},
        },
    }
    return _send(gym, settings_row, payload, to_phone, "interactive_buttons", "", member, deduplication_key)


# ── Connection verification & test message ─────────────────────────────────────

def verify_connection(gym: Gym) -> WhatsAppSendResult:
    """
    Three-stage verification, all of which must pass before the
    connection is marked 'connected':
      1. Access token + phone number reachable (existing phone metadata call)
      2. Business Account reachable + template list retrievable
      3. Every name in REQUIRED_TEMPLATES is present AND status=='APPROVED'
 
    Returns a structured `response` dict in both outcomes (point 3):
      success: {"phone_verified": True, "business_account_reachable": True,
                "templates_found": [...], "ready": True}
      failure: {"phone_verified": bool, "business_account_reachable": bool,
                "missing_templates": [...]}   (whichever stage failed)
 
    Failing template verification does NOT mark the connection
    successful — status stays/becomes 'error' via mark_error(), with a
    specific, actionable message naming the missing template(s), so an
    admin discovers this during setup rather than at first send.
    """
    settings_row = _get_configured_settings(gym)
    transport = get_transport(settings_row)
 
    # ── Stage 1: phone / credentials ────────────────────────────────────
    try:
        phone_body = transport.get_phone_metadata()
        phone_body.pop("_status_code", None)
    except WhatsAppTransportError as exc:
        settings_row.mark_error(str(exc))
        logger.warning("WhatsApp verify_connection: phone check failed gym=%s error=%s", gym.gym_code, exc)
        return WhatsAppSendResult(
            success=False, error=str(exc), status_code=exc.status_code,
            response={"phone_verified": False, "business_account_reachable": False},
        )
 
    # ── Stage 2 + 3: Business Account reachable + required templates ───
    try:
        templates = transport.list_templates()
    except WhatsAppTransportError as exc:
        error_msg = f"Phone verified, but Business Account/templates could not be checked: {exc}"
        settings_row.mark_error(error_msg)
        logger.warning("WhatsApp verify_connection: template list failed gym=%s error=%s", gym.gym_code, exc)
        return WhatsAppSendResult(
            success=False, error=error_msg, status_code=exc.status_code,
            response={"phone_verified": True, "business_account_reachable": False},
        )
 
    approved_names = {t["name"] for t in templates if t["status"] == "APPROVED"}
    missing = [name for name in REQUIRED_TEMPLATES if name not in approved_names]
 
    if missing:
        error_msg = "Missing approved WhatsApp templates:\n" + "\n".join(f"- {name}" for name in missing)
        settings_row.mark_error(error_msg)
        logger.warning("WhatsApp verify_connection: missing templates gym=%s missing=%s", gym.gym_code, missing)
        return WhatsAppSendResult(
            success=False, error=error_msg,
            response={
                "phone_verified": True,
                "business_account_reachable": True,
                "missing_templates": missing,
            },
        )
 
    settings_row.mark_connected()
    logger.info("WhatsApp connection verified gym=%s templates_ok=%s", gym.gym_code, sorted(approved_names))
    return WhatsAppSendResult(
        success=True,
        response={
            "phone_verified": True,
            "business_account_reachable": True,
            "templates_found": sorted(approved_names),
            "ready": True,
        },
    )


def _test_message_rate_limit_key(gym: Gym) -> str:
    return f"whatsapp_test_rate:{gym.id}"


def _enforce_test_message_rate_limit(gym: Gym) -> None:
    """
    Point 6 — fixed-window rate limit via cache (Redis-backed per
    settings.py CACHES config, no DB polling). cache.add is atomic: only
    the first caller in a window actually sets the initial value, so
    concurrent requests can't both "win" the first slot.
    """
    key = _test_message_rate_limit_key(gym)
    added = cache.add(key, 1, timeout=_TEST_MESSAGE_RATE_WINDOW_SECONDS)
    if added:
        return  # first test message in this window
    try:
        current = cache.incr(key)
    except ValueError:
        # Key expired between add() and incr() — treat as a fresh window.
        cache.add(key, 1, timeout=_TEST_MESSAGE_RATE_WINDOW_SECONDS)
        return
    if current > _TEST_MESSAGE_RATE_LIMIT:
        raise WhatsAppRateLimitExceeded(
            f"Test message rate limit exceeded for gym '{gym.gym_code}' "
            f"({_TEST_MESSAGE_RATE_LIMIT} per {_TEST_MESSAGE_RATE_WINDOW_SECONDS}s)."
        )


def send_test_message(gym: Gym, to_phone: str) -> WhatsAppSendResult:
    _enforce_test_message_rate_limit(gym)
    _validate_phone(to_phone)

    return send_template(
        gym=gym,
        to_phone=to_phone,
        template_name=TEMPLATE_MEMBERSHIP_EXPIRY_BEFORE,
        language_code="en",
        components=[
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "Sheikh Azmatulla"},
                    {"type": "text", "text": gym.gym_name},
                    {"type": "text", "text": "31 July 2026"},
                ],
            }
        ],
    )


# ── Bulk sending (Celery-ready abstraction, synchronous today; unchanged shape from Step 3) ──

def _synchronous_dispatch(send_fn: Callable[..., WhatsAppSendResult], *args, **kwargs) -> WhatsAppSendResult:
    try:
        return send_fn(*args, **kwargs)
    except WhatsAppError as exc:
        logger.exception("WhatsApp bulk dispatch: configuration/validation error")
        return WhatsAppSendResult(success=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — intentional isolation boundary
        logger.exception("WhatsApp bulk dispatch: unexpected error")
        return WhatsAppSendResult(success=False, error=f"Unexpected error: {exc}")


def send_bulk_messages(
    gym: Gym, recipients: list[dict], *, template_name: str, language_code: str = "en",
    components_builder: Optional[Callable[[dict], list]] = None,
    deduplication_key_builder: Optional[Callable[[dict], str]] = None,
    dispatch: Callable = _synchronous_dispatch,
) -> BulkSendSummary:
    """
    `deduplication_key_builder`: optional callable(recipient_dict) -> str,
    so bulk campaigns (e.g. "remind everyone expiring this week") get
    the same at-most-once guarantee as single-event triggers. If omitted,
    bulk messages are NOT deduplicated (matches a genuine one-off
    announcement blast where re-sending is an explicit user action).
    """
    results = []
    for i, recipient in enumerate(recipients):
        components = components_builder(recipient) if components_builder else None
        dedup_key = deduplication_key_builder(recipient) if deduplication_key_builder else ""
        result = dispatch(
            send_template, gym, recipient["phone"], template_name,
            language_code=language_code, components=components,
            member=recipient.get("member"), deduplication_key=dedup_key,
        )
        results.append(result)
        if dispatch is _synchronous_dispatch and i < len(recipients) - 1:
            time.sleep(_BULK_SEND_DELAY_SECONDS)

    sent = sum(1 for r in results if r.success and not r.skipped_duplicate)
    skipped = sum(1 for r in results if r.skipped_duplicate)
    failed = len(results) - sent - skipped
    summary = BulkSendSummary(total=len(results), sent=sent, failed=failed, skipped_duplicates=skipped, results=results)
    logger.info("WhatsApp bulk send complete gym=%s total=%d sent=%d failed=%d skipped_dupes=%d",
                gym.gym_code, summary.total, summary.sent, summary.failed, summary.skipped_duplicates)
    return summary

def normalize_phone_to_e164(raw_phone: str, *, default_country_code: str = None) -> str:
    """
    Best-effort conversion of EnterGYM's stored member phone format
    (bare 10-digit Indian mobile, e.g. "9876543210") to E.164
    ("+919876543210"). If `raw_phone` already starts with '+', it's
    assumed to already be E.164 and returned unchanged.
 
    ASSUMPTION FLAGGED FOR CONFIRMATION: defaults to
    settings.WHATSAPP_DEFAULT_COUNTRY_CODE (or '+91' if unset). This is
    correct for India-only tenants but will silently produce a wrong
    number for any future non-Indian gym — there is currently no
    per-gym country code field to do this correctly for a multi-country
    tenant base.
    """
    if not raw_phone:
        return raw_phone
    raw_phone = raw_phone.strip()
    if raw_phone.startswith('+'):
        return raw_phone
    code = default_country_code or getattr(settings, "WHATSAPP_DEFAULT_COUNTRY_CODE", "+91")
    return f"{code}{raw_phone}"
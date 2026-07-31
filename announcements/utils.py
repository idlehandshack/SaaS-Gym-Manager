"""
announcements/utils.py

Wires Announcement publishing into the EXISTING notification stack —
Shop.notifications.send_push_to_tokens (FCM) and
notifications.utils.send_web_push (Web Push) — rather than inventing a
third channel implementation. No new Firebase/VAPID code lives here.
"""

import logging
import re

from django.utils import timezone
from django.utils.html import strip_tags

from AuthFit.models import UserDevice
from Shop.notifications import send_push_to_tokens
from notifications.utils import send_web_push

logger = logging.getLogger(__name__)

PUSH_CHANNEL_ID = 'entergym_announcements'
MAX_BODY_LEN = 140

# Very small allow-list sanitizer for the rich-text description field.
# The project doesn't currently depend on bleach, so this strips anything
# that isn't in the allow-list rather than pulling in a new dependency.
_ALLOWED_TAGS_RE = re.compile(
    r'</?(?:p|br|b|strong|i|em|u|ul|ol|li|a|h[1-4]|span|blockquote)(\s[^>]*)?>',
    re.IGNORECASE,
)
_SCRIPT_STYLE_RE = re.compile(r'<(script|style)[^>]*>.*?</\1>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r'<[^>]+>')


def sanitize_rich_text(html: str) -> str:
    """
    Minimal defense-in-depth sanitizer: drop <script>/<style> blocks entirely,
    then strip any tag not on the allow-list (keeping allow-listed tags as-is).
    This is NOT a substitute for a proper sanitizer if the project later adds
    bleach/nh3 — it exists so a malicious paste can't inject a <script> tag
    through the announcement description field.
    """
    if not html:
        return html
    html = _SCRIPT_STYLE_RE.sub('', html)

    def _keep_or_strip(match):
        return match.group(0)

    # Strip every tag, then re-allow the safe subset by re-inserting matches
    # found via the allow-list regex against the original string is fragile;
    # simplest safe approach: strip all tags NOT in allow-list one at a time.
    allowed_spans = [(m.start(), m.end()) for m in _ALLOWED_TAGS_RE.finditer(html)]
    if not allowed_spans:
        return strip_tags(html)

    out = []
    last = 0
    for tag in _TAG_RE.finditer(html):
        if any(s <= tag.start() < e for s, e in allowed_spans):
            continue  # allowed tag — leave in place, handled by final pass below
        out.append(html[last:tag.start()])
        last = tag.end()
    out.append(html[last:])
    return ''.join(out)


def build_push_body(description_html: str) -> str:
    text = strip_tags(description_html or '').strip()
    text = re.sub(r'\s+', ' ', text)
    if len(text) > MAX_BODY_LEN:
        text = text[:MAX_BODY_LEN - 1].rstrip() + '…'
    return text


def send_announcement_push(announcement) -> int:
    """
    Fan-out an announcement to every targeted member's FCM + web-push
    channels. Returns the number of successful deliveries (best-effort —
    mirrors the "count successes, never let one bad device abort the run"
    pattern from AuthFit/notifications.py).

    Idempotency: caller (publish action / scheduler) is responsible for
    only invoking this once per announcement — see views.publish_announcement
    and management command `publish_scheduled_announcements`.
    """
    if not announcement.send_push:
        return 0

    gym_code = getattr(announcement.gym, 'gym_code', str(announcement.gym_id))
    title = announcement.title[:100]
    body = build_push_body(announcement.description)
    data = {
        "announcement_id": str(announcement.id),
        "gym_id":          str(announcement.gym_id),
        "screen":          "AnnouncementDetail",
        "type":            "announcement",
        "category":        announcement.announcement_type,
        "priority":        announcement.priority,
        "deep_link":       announcement.get_absolute_url(),
    }

    enrollment_qs = announcement.audience_enrollment_queryset().select_related('user')
    user_ids = list(enrollment_qs.values_list('user_id', flat=True).distinct())

    if not user_ids:
        logger.info("send_announcement_push: no targeted members announcement=%s gym=%s",
                    announcement.id, gym_code)
        return 0

    successes = 0

    # ── FCM (mobile) — one multicast call per gym, tokens batched ─────────
    try:
        tokens = list(
            UserDevice.objects
            .filter(gym=announcement.gym, user_id__in=user_ids, active=True)
            .values_list('fcm_token', flat=True)
        )
        if tokens:
            fcm_ok = send_push_to_tokens(
                tokens=tokens, title=title, body=body, data=data,
                channel_id=PUSH_CHANNEL_ID,
            )
            successes += fcm_ok
            logger.info("Announcement FCM sent announcement=%s gym=%s successes=%s",
                        announcement.id, gym_code, fcm_ok)
    except Exception:
        logger.exception("send_announcement_push: FCM fan-out failed announcement=%s", announcement.id)

    # ── Web push — per user, isolated so one bad subscription never
    #     aborts the rest ────────────────────────────────────────────────
    from django.contrib.auth.models import User
    for user in User.objects.filter(id__in=user_ids, is_active=True):
        try:
            successes += send_web_push(user=user, title=title, body=body, url=announcement.get_absolute_url())
        except Exception:
            logger.exception(
                "send_announcement_push: web push failed user=%s announcement=%s",
                user.id, announcement.id,
            )
            continue

    announcement.push_sent_at = timezone.now()
    announcement.push_sent_count = successes
    announcement.save(update_fields=['push_sent_at', 'push_sent_count'])

    return successes

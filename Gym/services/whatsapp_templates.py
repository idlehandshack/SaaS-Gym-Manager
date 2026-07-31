# Gym/services/whatsapp_templates.py
"""
WhatsApp Meta template registry for EnterGYM's business notifications.

Single source of truth for which templates exist AND which are
mandatory for a gym's WhatsApp connection to be considered ready
(REQUIRED_TEMPLATES, consumed by whatsapp_service.verify_connection()).

Only the two triggers that have a real call site in the codebase today
are defined here (membership_expiry_before / membership_expiry_after —
both fire from AuthFit/notifications.py's send_expiry_reminders()).

payment_success / new_member / announcements / invoices / receipts /
attendance are DELIBERATELY NOT defined here — per explicit instruction,
no constants, builders, or registry entries for a notification type
until its business trigger and payload are implemented elsewhere in the
codebase. Add a new TEMPLATE_* constant + a components builder + a
REQUIRED_TEMPLATES entry together, only when that trigger is real.

IMPORTANT — template names must match EXACTLY what's been created and
approved in Meta Business Manager. Update these constants only if your
actual approved names differ — nothing else needs to change, since
verify_connection() and the notification callers both import from here
rather than hardcoding names.
"""

from __future__ import annotations
from datetime import date, datetime
TEMPLATE_MEMBERSHIP_EXPIRY_BEFORE = "membership_expiry_before_v2"
TEMPLATE_MEMBERSHIP_EXPIRY_AFTER = "membership_expiry_after_v2"

# Every template a gym's WhatsApp Business Account MUST have approved
# before its connection is considered "ready" — checked by
# whatsapp_service.verify_connection(). Extend this list only when a
# corresponding TEMPLATE_* constant + components builder above it
# already exists AND that notification type is actually wired to a
# real business event.
REQUIRED_TEMPLATES = [
    TEMPLATE_MEMBERSHIP_EXPIRY_BEFORE,
    TEMPLATE_MEMBERSHIP_EXPIRY_AFTER,
]


def _text_param(value) -> dict:
    return {"type": "text", "text": str(value)}

def _format_expiry_date(expiry_date) -> str:
    """
    Formats a date/datetime for template display: '31 July 2026'.
    Null-safe — if expiry_date is None (shouldn't normally happen since
    this is only called with enr.DueDate, but defensive per spec),
    returns a neutral placeholder instead of raising, so a single bad
    record can never crash a template send or the whole cron run.
    """
    if expiry_date is None:
        return "N/A"
    try:
        return expiry_date.strftime("%d %B %Y")
    except Exception:
        return "N/A"
    
def build_expiry_before_components(*, member_name: str, gym_name: str,  expiry_date: date | datetime | None,) -> list:
    """
    Matches the spec's template text: "Hi {{member_name}}, your
    membership at {{gym_name}} expires tomorrow ({{expiry_date}}).
    Please renew..."
    Meta template components use positional body parameters — order
    here MUST match the order of {{1}}, {{2}}, {{3}} in the approved
    template: member_name -> gym_name -> expiry_date.
    """
    return [
        {
            "type": "body",
            "parameters": [
                _text_param(member_name),
                _text_param(gym_name),
                _text_param(_format_expiry_date(expiry_date)),
            ],
        }
    ]


def build_expiry_after_components(*, member_name: str, gym_name: str,  expiry_date: date | datetime | None,) -> list:
    """Matches: "Hi {{member_name}}, your membership at {{gym_name}}
    expired on {{expiry_date}}. Please visit the gym to renew."
    Same positional order as build_expiry_before_components."""
    return [
        {
            "type": "body",
            "parameters": [
                _text_param(member_name),
                _text_param(gym_name),
                _text_param(_format_expiry_date(expiry_date)),
            ],
        }
    ]
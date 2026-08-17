"""
billing/services/invoice_share.py
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

from django.conf import settings
from django.urls import reverse

from billing.models import Invoice
from billing.models import InvoiceShareToken

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Phone normalization
# ──────────────────────────────────────────────────────────────────────────
class InvalidPhoneNumber(Exception):
    pass


def normalize_whatsapp_number(raw_phone: str, default_country_code: str = "91") -> str:
    if not raw_phone or not raw_phone.strip():
        raise InvalidPhoneNumber("Phone number is empty.")

    digits = re.sub(r"[^\d]", "", raw_phone)

    if not digits:
        raise InvalidPhoneNumber(f"Phone number '{raw_phone}' has no digits.")

    cc = default_country_code

    if len(digits) == 10:
        normalized = f"{cc}{digits}"
    elif len(digits) == 12 and digits.startswith(cc):
        normalized = digits
    elif len(digits) == 11 and digits.startswith("0"):
        normalized = f"{cc}{digits[1:]}"
    elif len(digits) > 12 and digits.startswith(cc * 2):
        normalized = digits[len(cc):]
    else:
        raise InvalidPhoneNumber(
            f"Phone number '{raw_phone}' does not resolve to a valid "
            f"{len(cc) + 10}-digit WhatsApp number."
        )

    if len(normalized) != len(cc) + 10 or not normalized.isdigit():
        raise InvalidPhoneNumber(f"Phone number '{raw_phone}' normalized to an invalid value.")

    return normalized


# ──────────────────────────────────────────────────────────────────────────
# Public invoice URL / share token
# ──────────────────────────────────────────────────────────────────────────
def get_or_create_share_token(invoice: Invoice, staff_user=None) -> InvoiceShareToken:
    token_obj, _created = InvoiceShareToken.objects.get_or_create(
        invoice=invoice, defaults={"created_by": staff_user}
    )
    return token_obj


def build_public_invoice_url(token_obj: InvoiceShareToken, request=None) -> str:
    path = reverse("billing:public_invoice_view", kwargs={"token": token_obj.token})
    if request is not None:
        base = request.build_absolute_uri(path)
        if not settings.DEBUG and base.startswith("http://"):
            base = "https://" + base[len("http://"):]
        return base
    site_url = getattr(settings, "SITE_URL", "").rstrip("/")
    return f"{site_url}{path}"


# ──────────────────────────────────────────────────────────────────────────
# WhatsApp message + deep link
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class WhatsAppShareLink:
    wa_url: str
    message: str
    normalized_phone: str


def build_invoice_whatsapp_message(invoice: Invoice, public_url: str) -> str:
    gym_name = invoice.gym.gym_name
    lines = [
        f"Hi, here is your invoice from {gym_name}.",
        f"Invoice No: {invoice.invoice_number}",
    ]

    payment = getattr(invoice, "related_payment", None)
    if payment is not None and payment.pending_amount and payment.pending_amount > 0:
        lines.append(f"Paid: ₹{payment.paid_amount} | Remaining: ₹{payment.pending_amount}")
    else:
        lines.append(f"Amount: ₹{invoice.grand_total}")

    lines.append(f"View / download: {public_url}")
    return "\n".join(lines)


def build_whatsapp_deep_link(invoice: Invoice, staff_user=None, request=None) -> WhatsAppShareLink:
    member_phone = invoice.customer_phone or (invoice.member.phone if invoice.member_id else "")
    normalized = normalize_whatsapp_number(member_phone)

    token_obj = get_or_create_share_token(invoice, staff_user=staff_user)
    public_url = build_public_invoice_url(token_obj, request=request)
    message = build_invoice_whatsapp_message(invoice, public_url)

    wa_url = f"https://wa.me/{normalized}?text={quote(message)}"

    token_obj.mark_shared()

    logger.info(
        "Invoice share link generated for invoice=%s gym=%s staff=%s",
        invoice.invoice_number, invoice.gym_id, getattr(staff_user, "id", None),
    )

    return WhatsAppShareLink(wa_url=wa_url, message=message, normalized_phone=normalized)

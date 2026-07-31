"""
AuthFit/email_utils.py
-----------------------
Sends transactional email via Brevo's REST API (HTTPS, port 443) instead of
SMTP — DigitalOcean blocks outbound SMTP ports 25/465/587 on droplets as of
March 2025, so this avoids that entirely.
"""
import os
import logging
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

logger = logging.getLogger(__name__)


def send_transactional_email(subject, html_content, to_email, to_name=""):
    """
    Returns True on success, False on failure (never raises — caller decides
    what to do, e.g. still show a generic success message to the end user
    while logging the real error server-side).
    """
    api_key = os.environ.get('BREVO_API_KEY', '')
    if not api_key:
        logger.error("BREVO_API_KEY is not set — cannot send email")
        return False

    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = api_key

    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
        sib_api_v3_sdk.ApiClient(configuration)
    )

    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email, "name": to_name or to_email}],
        sender={
            "email": os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@entergym.in'),
            "name": "EnterGYM",
        },
        subject=subject,
        html_content=html_content,
    )

    try:
        api_instance.send_transac_email(send_smtp_email)
        logger.info("Brevo email sent — to=%s subject=%s", to_email, subject)
        return True
    except ApiException:
        logger.exception("Brevo API email send failed — to=%s subject=%s", to_email, subject)
        return False
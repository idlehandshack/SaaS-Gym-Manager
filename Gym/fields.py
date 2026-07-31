# Gym/fields.py
"""
Generic reusable encrypted-field support for EnterGYM.

This is intentionally NOT WhatsApp-specific. Any model that needs to store
a secret at rest (WhatsApp permanent access tokens today; SMS provider
API keys, email provider SMTP passwords, or payment gateway secrets in
future integrations) should import EncryptedTextField / EncryptedCharField
from here rather than each app rolling its own crypto.

Encryption: Fernet (symmetric, authenticated) from the `cryptography`
package. Fernet already ships as a transitive dependency of `pywebpush`
(see notifications/utils.py), so no new third-party surface is being
introduced — it is being made explicit and added to requirements.txt.

Key management
--------------
Reads settings.ENCRYPTION_KEY (a urlsafe-base64 Fernet key,
i.e. the output of Fernet.generate_key()).

This is a project-wide encryption key used by all encrypted fields.
It is intentionally generic so future integrations (WhatsApp, SMS,
SMTP, payment gateways, OAuth credentials, etc.) reuse the same
encryption infrastructure., despite the generic naming
of this module — the environment variable name was specified explicitly
for this feature and is not renamed here. If a project wants a
differently-named key for a different integration later, that can be
layered in via `get_fernet_key()` without touching the field classes.

Generating a key (run once per environment, store in your secrets
manager / .env, never commit it):

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Rotation
--------
This is single-key only. If you need zero-downtime key rotation later,
`cryptography.fernet.MultiFernet` is a drop-in upgrade path in
`get_fernet()` below — not implemented now since nothing in the spec
calls for it, and adding it speculatively would be unrequested scope.

Failure mode
-------------
If `WHATSAPP_ENCRYPTION_KEY` is missing or malformed, this raises
`ImproperlyConfigured` at first use (not at import time / Django boot),
so management commands that don't touch encrypted fields (e.g.
`collectstatic`) aren't broken by a missing key in an env where this
feature isn't used yet.
"""

from __future__ import annotations

import functools
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    """
    Build (and cache) the Fernet instance from settings.ENCRYPTION_KEY.

    Cached with lru_cache so we don't re-parse/validate the key on every
    field access — the key is static for the lifetime of the process.
    """
    key = getattr(settings, "ENCRYPTION_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "settings.ENCRYPTION_KEY is not set. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"\n"
            "and set it as the ENCRYPTION_KEY environment variable."
        )
    if isinstance(key, str):
        key = key.encode()
    try:
        return Fernet(key)
    except Exception as exc:
        raise ImproperlyConfigured(
            "settings.ENCRYPTION_KEY is not a valid Fernet key."
        ) from exc


class EncryptedTextFieldMixin:
    """
    Shared encrypt/decrypt behaviour for TextField- and CharField-based
    encrypted fields. Ciphertext is stored as opaque text in the DB column
    (Fernet tokens are urlsafe-base64 ASCII, so a plain TextField/CharField
    column is sufficient — no special DB column type needed).
    """

    def get_prep_value(self, value):
        """Called when writing to the DB — encrypt plaintext -> ciphertext."""
        if value is None or value == "":
            return value
        if isinstance(value, str):
            value = value.encode("utf-8")
        token = get_fernet().encrypt(value)
        return token.decode("utf-8")

    def from_db_value(self, value, expression, connection):
        """Called when reading from the DB — decrypt ciphertext -> plaintext."""
        return self._decrypt(value)

    def to_python(self, value):
        """
        Called during deserialization/form-cleaning as well as model
        construction from kwargs. If `value` is already plaintext (e.g. an
        in-memory model instance that hasn't hit the DB yet), decrypting it
        would raise InvalidToken — in that case, return it unchanged.
        """
        if value is None or isinstance(value, str) is False:
            return value
        return self._decrypt(value, tolerate_plaintext=True)

    def _decrypt(self, value, tolerate_plaintext: bool = False):
        if value is None or value == "":
            return value
        try:
            plaintext = get_fernet().decrypt(value.encode("utf-8"))
            return plaintext.decode("utf-8")
        except InvalidToken:
            if tolerate_plaintext:
                # Value never round-tripped through the DB yet (e.g. a
                # freshly-constructed unsaved instance) — treat as already
                # plaintext rather than raising.
                return value
            logger.error(
                "EncryptedTextField: failed to decrypt stored value — "
                "wrong ENCRYPTION_KEY or corrupted data."
            )
            raise


class EncryptedTextField(EncryptedTextFieldMixin, models.TextField):
    """
    Drop-in TextField replacement that transparently encrypts at rest.

    Usage:
        from Gym.fields import EncryptedTextField

        class GymWhatsAppSettings(models.Model):
            permanent_access_token = EncryptedTextField(blank=True, default="")
    """
    pass


class EncryptedCharField(EncryptedTextFieldMixin, models.CharField):
    """
    Drop-in CharField replacement for shorter secrets where a max_length
    is meaningful for form validation (note: the *stored* ciphertext is
    longer than the plaintext, so max_length here validates the
    plaintext length via clean(), not the DB column width — see
    get_prep_value below, which does not enforce max_length on the
    encrypted value).
    """

    def get_prep_value(self, value):
        # Skip CharField's own max_length-oriented prep — only the
        # mixin's encrypt-on-write behaviour applies here.
        return EncryptedTextFieldMixin.get_prep_value(self, value)
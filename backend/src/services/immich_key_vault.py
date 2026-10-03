"""Encrypts players' Immich API keys at rest (persistence/users.py's
UserModel.immich_api_key_encrypted) - a database dump alone must not hand out working keys to
everyone's photo libraries.

Fernet (AES-128-CBC + HMAC-SHA256) keyed by IMMICH_KEY_ENCRYPTION_SECRET, falling back to a key
derived from JWT_SECRET when that's unset. The two derivations are domain-separated, so neither
secret's Fernet key equals anything else derived from it. Rotating whichever secret is in use makes
every stored key undecryptable - decrypt() then returns None and those players are simply asked to
link their key again (services/errors.py's ImmichNotLinkedError), nothing breaks harder than that.
"""

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from config import Settings, get_settings

logger = logging.getLogger(__name__)

_CONTEXT = b"immich-minigames/immich-api-key/v1:"


@lru_cache(maxsize=1)
def _fernet_for(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(_CONTEXT + secret.encode()).digest()))


def _fernet(settings: Settings) -> Fernet:
    if settings.immich_key_encryption_secret:
        return _fernet_for(settings.immich_key_encryption_secret)
    return _fernet_for(settings.jwt_secret)


def encrypt(api_key: str, settings: Settings | None = None) -> str:
    return _fernet(settings or get_settings()).encrypt(api_key.encode()).decode()


def decrypt(token: str | None, settings: Settings | None = None) -> str | None:
    """None for a missing token or one encrypted under a since-rotated secret (see module docstring)."""
    if not token:
        return None
    try:
        return _fernet(settings or get_settings()).decrypt(token.encode()).decode()
    except InvalidToken:
        logger.warning("stored Immich API key can't be decrypted - encryption secret rotated? player must relink")
        return None

"""services/immich_key_vault.py - pure, no DB."""

from config import get_settings
from services import immich_key_vault


def _settings(**overrides):
    return get_settings().model_copy(update=overrides)


class TestImmichKeyVault:
    def test_round_trips(self):
        settings = _settings(immich_key_encryption_secret="s1")
        token = immich_key_vault.encrypt("my-key", settings)

        assert token != "my-key"
        assert immich_key_vault.decrypt(token, settings) == "my-key"

    def test_rotated_secret_reads_as_unlinked_instead_of_raising(self):
        token = immich_key_vault.encrypt("my-key", _settings(immich_key_encryption_secret="s1"))

        assert immich_key_vault.decrypt(token, _settings(immich_key_encryption_secret="s2")) is None

    def test_falls_back_to_jwt_secret(self):
        settings = _settings(immich_key_encryption_secret=None, jwt_secret="jwt")
        token = immich_key_vault.encrypt("my-key", settings)

        assert immich_key_vault.decrypt(token, settings) == "my-key"
        # Without an explicit secret, rotating JWT_SECRET makes stored keys unreadable (documented).
        assert immich_key_vault.decrypt(token, _settings(immich_key_encryption_secret=None, jwt_secret="other")) is None

    def test_missing_token_is_none(self):
        assert immich_key_vault.decrypt(None) is None

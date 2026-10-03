"""services/immich/account.py's verify_api_key against a fake Immich (httpx.MockTransport) - no
real Immich, no DB."""

from uuid import uuid4

import httpx
import pytest

from config import get_settings
from services.errors import ImmichKeyRejectedError
from services.immich import account

_USER_ID = uuid4()


def _fake_immich(permissions=None, key_status=200, user_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "the-key"
        if request.url.path == "/api/api-keys/me":
            return httpx.Response(key_status, json={"id": "k", "name": "minigames", "permissions": permissions or []})
        if request.url.path == "/api/users/me":
            return httpx.Response(user_status, json={"id": str(_USER_ID), "name": "Ana", "email": "ana@x"})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def immich(monkeypatch):
    def install(**kwargs):
        monkeypatch.setattr(account, "_get_http_client", lambda: _fake_immich(**kwargs))

    return install


class TestVerifyApiKey:
    def test_minimal_permissions_are_enough(self, immich):
        immich(permissions=["user.read", "asset.view", "person.read"])

        result = account.verify_api_key(get_settings(), "the-key")

        assert (result.user_id, result.name, result.email) == (_USER_ID, "Ana", "ana@x")

    def test_all_permission_is_enough(self, immich):
        immich(permissions=["all"])

        assert account.verify_api_key(get_settings(), "the-key").user_id == _USER_ID

    def test_names_the_missing_permissions(self, immich):
        immich(permissions=["user.read"])

        with pytest.raises(ImmichKeyRejectedError, match="asset.view, person.read"):
            account.verify_api_key(get_settings(), "the-key")

    def test_invalid_key(self, immich):
        immich(key_status=401)

        with pytest.raises(ImmichKeyRejectedError, match="rejected"):
            account.verify_api_key(get_settings(), "the-key")

    def test_unreachable_immich(self, monkeypatch):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        monkeypatch.setattr(account, "_get_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))

        with pytest.raises(ImmichKeyRejectedError, match="could not reach"):
            account.verify_api_key(get_settings(), "the-key")

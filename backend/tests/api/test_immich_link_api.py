"""Linking a player's own Immich account (PUT/DELETE /auth/me/immich) and what an unlinked player
gets from everything that needs one. Uses `unlinked_client` (conftest.py) - the real per-player
get_immich_service, not the unscoped override every other API test plays with. Immich's own REST API
is never called: services/immich/account.py's verify_api_key is monkeypatched per test."""

import uuid
from uuid import uuid4

import pytest
from conftest import mint_invite_code

import api.auth_api
from persistence.base import get_session_factory
from persistence.users import UserModel
from services import immich_key_vault
from services.errors import ImmichKeyRejectedError
from services.immich.account import ImmichAccount

_ACCOUNT = ImmichAccount(user_id=uuid4(), name="Ana", email="ana@immich.example")


def _register(client) -> uuid.UUID:
    unique = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"link-{unique}@example.com",
            "username": f"link-{unique}",
            "full_name": "Link Test",
            "password": "correct-horse-battery-staple",
            "invite_code": mint_invite_code(),
        },
    )
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


def _stored_user(user_id: uuid.UUID) -> UserModel:
    session = get_session_factory()()
    try:
        return session.get(UserModel, user_id)
    finally:
        session.close()


@pytest.fixture
def accept_any_key(monkeypatch):
    accounts: dict[str, ImmichAccount] = {}

    def fake_verify(settings, api_key):
        return accounts.get(api_key, _ACCOUNT)

    monkeypatch.setattr(api.auth_api, "verify_api_key", fake_verify)
    return accounts


class TestUnlinkedPlayer:
    def test_registers_without_an_immich_account(self, unlinked_client):
        _register(unlinked_client)

        assert unlinked_client.get("/api/v1/auth/me").json()["immich_account"] is None

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/games"),
            ("get", "/api/v1/persons/search?query=a"),
            ("get", f"/api/v1/assets/{uuid4()}/thumbnail"),
            ("get", "/api/v1/daily"),
        ],
    )
    def test_anything_reading_immich_answers_immich_not_linked(self, unlinked_client, method, path):
        _register(unlinked_client)

        kwargs = {"json": {"type": "immichdle", "mode": "person"}} if method == "post" else {}
        response = getattr(unlinked_client, method)(path, **kwargs)

        assert response.status_code == 409
        assert response.json()["detail"] == "immich_not_linked"

    def test_scores_still_work_unlinked(self, unlinked_client):
        _register(unlinked_client)

        assert unlinked_client.get("/api/v1/games/records").status_code == 200


class TestLinkImmich:
    def test_link_stores_the_key_encrypted_and_never_returns_it(self, unlinked_client, accept_any_key):
        user_id = _register(unlinked_client)

        response = unlinked_client.put("/api/v1/auth/me/immich", json={"api_key": "  secret-key  "})

        assert response.status_code == 200
        assert response.json()["immich_account"] == {"name": "Ana", "email": "ana@immich.example"}
        assert "secret-key" not in response.text
        stored = _stored_user(user_id)
        assert stored.immich_user_id == _ACCOUNT.user_id
        assert stored.immich_api_key_encrypted != "secret-key"
        assert immich_key_vault.decrypt(stored.immich_api_key_encrypted) == "secret-key"

    def test_linked_player_can_reach_immich_backed_routes(self, unlinked_client, accept_any_key):
        _register(unlinked_client)
        unlinked_client.put("/api/v1/auth/me/immich", json={"api_key": "k"})

        # Not a 409 anymore - the (random, nonexistent) Immich user just has no matching people.
        response = unlinked_client.get("/api/v1/persons/search?query=a")

        assert response.status_code == 200

    def test_rejected_key_is_a_400_with_the_reason(self, unlinked_client, monkeypatch):
        _register(unlinked_client)

        def reject(settings, api_key):
            raise ImmichKeyRejectedError("API key is missing permissions: person.read")

        monkeypatch.setattr(api.auth_api, "verify_api_key", reject)

        response = unlinked_client.put("/api/v1/auth/me/immich", json={"api_key": "narrow"})

        assert response.status_code == 400
        assert "person.read" in response.json()["detail"]
        assert unlinked_client.get("/api/v1/auth/me").json()["immich_account"] is None

    def test_unlink_forgets_the_key(self, unlinked_client, accept_any_key):
        user_id = _register(unlinked_client)
        unlinked_client.put("/api/v1/auth/me/immich", json={"api_key": "k"})

        response = unlinked_client.delete("/api/v1/auth/me/immich")

        assert response.status_code == 200
        assert response.json()["immich_account"] is None
        assert _stored_user(user_id).immich_api_key_encrypted is None
        assert unlinked_client.get("/api/v1/persons/search?query=a").status_code == 409

    def test_linking_a_different_immich_user_clears_the_skin(self, unlinked_client, accept_any_key):
        user_id = _register(unlinked_client)
        accept_any_key["first"] = ImmichAccount(user_id=uuid4(), name="A", email="a@x")
        accept_any_key["second"] = ImmichAccount(user_id=uuid4(), name="B", email="b@x")
        unlinked_client.put("/api/v1/auth/me/immich", json={"api_key": "first"})
        session = get_session_factory()()
        try:
            session.get(UserModel, user_id).skin_person_id = uuid4()
            session.commit()
        finally:
            session.close()

        unlinked_client.put("/api/v1/auth/me/immich", json={"api_key": "first"})
        assert _stored_user(user_id).skin_person_id is not None
        unlinked_client.put("/api/v1/auth/me/immich", json={"api_key": "second"})
        assert _stored_user(user_id).skin_person_id is None

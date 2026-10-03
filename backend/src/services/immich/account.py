"""Validates a player's Immich API key before it's linked to their account (api/auth_api.py's
PUT /auth/me/immich) - which Immich user it belongs to, and whether it carries every permission
this app needs. Same REST transport as images.py, never Postgres."""

from dataclasses import dataclass
from uuid import UUID

import httpx

from config import Settings
from services.errors import ImmichKeyRejectedError

from .images import _get_http_client

# Everything this app ever calls with a player's key: GET /users/me (user.read, here), GET
# /assets/{id}/thumbnail (asset.view) and GET /people/{id}/thumbnail (person.read). Keep in sync with
# docs/IMMICH_API_KEY.md.
REQUIRED_PERMISSIONS = ("user.read", "asset.view", "person.read")


@dataclass(frozen=True)
class ImmichAccount:
    user_id: UUID
    name: str
    email: str


def verify_api_key(settings: Settings, api_key: str) -> ImmichAccount:
    """The Immich account `api_key` belongs to. Raises ImmichKeyRejectedError if Immich can't be
    reached, rejects the key, or the key lacks one of REQUIRED_PERMISSIONS - checked up front via
    GET /api-keys/me (which needs no permission of its own), so a too-narrow key fails here rather
    than as broken thumbnails mid-game."""
    client = _get_http_client()
    headers = {"x-api-key": api_key}
    try:
        key_response = client.get(f"{settings.immich_server_url}/api/api-keys/me", headers=headers)
        if key_response.status_code in (401, 403):
            raise ImmichKeyRejectedError("Immich rejected this API key")
        key_response.raise_for_status()
        granted = set(key_response.json().get("permissions", []))
        if "all" not in granted:
            missing = [p for p in REQUIRED_PERMISSIONS if p not in granted]
            if missing:
                raise ImmichKeyRejectedError(f"API key is missing permissions: {', '.join(missing)}")

        user_response = client.get(f"{settings.immich_server_url}/api/users/me", headers=headers)
        user_response.raise_for_status()
        user = user_response.json()
    except httpx.HTTPStatusError as exc:
        raise ImmichKeyRejectedError(f"Immich answered {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise ImmichKeyRejectedError("could not reach Immich") from exc

    return ImmichAccount(user_id=UUID(user["id"]), name=user.get("name", ""), email=user.get("email", ""))

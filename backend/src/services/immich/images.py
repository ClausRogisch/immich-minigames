"""Image bytes via Immich's REST API - never Postgres, see the package docstring's "Dos formas de
hablar con Immich"."""

from functools import lru_cache
from uuid import UUID

import httpx

from config import Settings
from services.errors import ImmichNotLinkedError


# Reused across requests (pooled connections, one client) instead of opening a new connection per
# thumbnail. A bounded timeout means a slow/hung Immich never blocks a worker thread indefinitely.
@lru_cache(maxsize=1)
def _get_http_client() -> httpx.Client:
    return httpx.Client(timeout=10.0)


def _auth_headers(api_key: str | None) -> dict[str, str]:
    """Always the *player's* own key (see ImmichService) - Immich then refuses anything that player
    can't see in Immich itself, a second line of defense behind ._scope's query filters."""
    if not api_key:
        raise ImmichNotLinkedError()
    return {"x-api-key": api_key}


def get_asset_thumbnail(
    settings: Settings, api_key: str | None, asset_id: UUID, size: str = "preview"
) -> tuple[bytes, str]:
    """Fetches an asset's image bytes via Immich's REST API. `size="preview"` (~1440px JPEG
    derivative) rather than the default `thumbnail` (~250px, too small for fullscreen) or
    `original` (may be HEIC/RAW/video, not safely renderable in a browser <img>).

    Raises httpx.HTTPStatusError (e.g. 404 - no thumbnail, 401 - revoked API key, 403 - not
    visible to this key's user) or httpx.RequestError (Immich unreachable/timed out) - see
    api/api.py for how each maps to a response."""
    response = _get_http_client().get(
        f"{settings.immich_server_url}/api/assets/{asset_id}/thumbnail",
        params={"size": size},
        headers=_auth_headers(api_key),
    )
    response.raise_for_status()
    return response.content, response.headers.get("content-type", "image/jpeg")


def get_person_thumbnail(settings: Settings, api_key: str | None, person_id: UUID) -> tuple[bytes, str]:
    """Fetches a person's face thumbnail via Immich's REST API. Returns (image bytes, content-type).

    Raises httpx.HTTPStatusError (e.g. 404 - no thumbnail, 401 - revoked API key, 403 - not
    visible to this key's user) or httpx.RequestError (Immich unreachable/timed out) - see
    api/api.py for how each maps to a response."""
    response = _get_http_client().get(
        f"{settings.immich_server_url}/api/people/{person_id}/thumbnail",
        headers=_auth_headers(api_key),
    )
    response.raise_for_status()
    return response.content, response.headers.get("content-type", "image/jpeg")

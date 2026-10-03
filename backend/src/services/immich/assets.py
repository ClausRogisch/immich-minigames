"""Postgres queries over Immich's `asset`/`asset_exif` tables."""

import math
import random
import threading
import time
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy import Date, Select, cast, exists, extract, func, select
from sqlalchemy.engine import Connection, Engine, Row

from domain.asset import Asset
from persistence.immich_tables import asset, asset_exif, asset_file

from ._random import sample_by_id_pivot
from ._rows import row_to_asset
from ._scope import home_asset, shared_album_asset, visible_asset

MediaType = Literal["photo", "video", "any"]
LocationField = Literal["city", "country"]

# Shared albums can hold far more photos than a player's own library (a family album with thousands
# of photos next to a few hundred of their own), so randomly sampled photos draw from the two parts
# of a player's view (see ._scope) separately: shared-album-only photos get a share proportional to
# their pool size, but never more than this - the player's own and partners' photos stay the bulk
# of every game. A player with no home photos at all still gets everything from shared albums.
SHARED_ALBUM_MAX_SHARE = 0.3
# Pool sizes only steer the mix, they don't need to be exact - recounting them on every round would
# double the cost of a sample for no visible benefit.
_POOL_SIZES_TTL_SECONDS = 600
_pool_sizes: dict[UUID, tuple[float, int, int]] = {}
_pool_sizes_lock = threading.Lock()


def shared_album_share(home_count: int, shared_only_count: int, cap: float = SHARED_ALBUM_MAX_SHARE) -> float:
    """Probability that one sampled photo comes from shared albums - see SHARED_ALBUM_MAX_SHARE."""
    if shared_only_count == 0:
        return 0.0
    if home_count == 0:
        return 1.0
    return min(shared_only_count / (home_count + shared_only_count), cap)


def _eligible() -> list:
    return [asset.c.status == "active", asset.c.visibility == "timeline", asset.c.deletedAt.is_(None)]


def _pool_sizes_for(conn: Connection, user_id: UUID) -> tuple[int, int]:
    """(home, shared-album-only) eligible photo counts for this player, cached per process."""
    now = time.monotonic()
    with _pool_sizes_lock:
        cached = _pool_sizes.get(user_id)
    if cached is not None and now - cached[0] < _POOL_SIZES_TTL_SECONDS:
        return cached[1], cached[2]
    home = conn.execute(select(func.count()).select_from(asset).where(*_eligible(), home_asset(user_id))).scalar_one()
    shared_only = conn.execute(
        select(func.count()).select_from(asset).where(*_eligible(), shared_album_asset(user_id), ~home_asset(user_id))
    ).scalar_one()
    with _pool_sizes_lock:
        _pool_sizes[user_id] = (now, home, shared_only)
    return home, shared_only


def _sample_mixed(conn: Connection, stmt: Select, user_id: UUID, limit: int) -> list[Row]:
    """`limit` random rows of `stmt` (already restricted to visible_asset), mixing the player's home
    and shared-album-only photos per shared_album_share. Whichever part runs out is topped up from
    the other, so this returns as many rows as plain sampling would."""
    share = shared_album_share(*_pool_sizes_for(conn, user_id))
    want_shared = sum(1 for _ in range(limit) if random.random() < share)
    shared_stmt = stmt.where(~home_asset(user_id))
    home_stmt = stmt.where(home_asset(user_id))

    rows = sample_by_id_pivot(conn, shared_stmt, asset.c.id, want_shared)
    rows += sample_by_id_pivot(conn, home_stmt, asset.c.id, limit - len(rows))
    if len(rows) < limit:
        taken = [row.id for row in rows]
        top_up = shared_stmt.where(asset.c.id.notin_(taken)) if taken else shared_stmt
        rows += sample_by_id_pivot(conn, top_up, asset.c.id, limit - len(rows))
    random.shuffle(rows)
    return rows


def get_assets(
    engine: Engine,
    user_id: UUID | None,
    *,
    media_type: MediaType = "any",
    with_location: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    local_date: date | None = None,
    local_month: int | None = None,
    near_km: tuple[float, float, float] | None = None,
    randomize: bool = False,
    limit: int = 1,
    ids: frozenset[UUID] | None = None,
    exclude_ids: frozenset[UUID] = frozenset(),
) -> list[Asset]:
    has_thumbnail = exists(
        select(1).where(
            asset_file.c.assetId == asset.c.id,
            asset_file.c.type == "thumbnail",
        )
    )

    # True local calendar day of the shot, timezone-of-session-independent: localDateTime
    # is a timestamptz whose UTC rendering is the local wall time, so casting to date at
    # UTC recovers the local day (see immich_tables.py / domain/asset.py).
    local_date_expr = cast(func.timezone("UTC", asset.c.localDateTime), Date)

    stmt = (
        select(
            asset.c.id,
            asset.c.type,
            asset.c.fileCreatedAt,
            local_date_expr.label("localDate"),
            asset.c.originalFileName,
            asset.c.width,
            asset.c.height,
            asset.c.isFavorite,
            asset_exif.c.latitude,
            asset_exif.c.longitude,
            asset_exif.c.city,
            asset_exif.c.state,
            asset_exif.c.country,
        )
        .select_from(asset.outerjoin(asset_exif, asset_exif.c.assetId == asset.c.id))
        .where(
            asset.c.status == "active",
            asset.c.visibility == "timeline",
            asset.c.deletedAt.is_(None),
            visible_asset(user_id),
            has_thumbnail,
        )
    )

    if media_type == "photo":
        stmt = stmt.where(asset.c.type == "IMAGE")
    elif media_type == "video":
        stmt = stmt.where(asset.c.type == "VIDEO")

    if with_location is True:
        stmt = stmt.where(asset_exif.c.latitude.is_not(None), asset_exif.c.longitude.is_not(None))
    elif with_location is False:
        stmt = stmt.where(asset_exif.c.latitude.is_(None), asset_exif.c.longitude.is_(None))

    if date_from is not None:
        stmt = stmt.where(asset.c.fileCreatedAt >= date_from)
    if date_to is not None:
        stmt = stmt.where(asset.c.fileCreatedAt <= date_to)

    if local_date is not None:
        stmt = stmt.where(local_date_expr == local_date)
    if local_month is not None:
        stmt = stmt.where(extract("month", local_date_expr) == local_month)

    if near_km is not None:
        # Coarse lat/lon bounding-box prefilter (box, not circle) - callers that need the exact
        # circle (e.g. games/geoguessr.py's 500m rule) do a precise haversine post-filter
        # themselves. Degrees-per-km approximation: 111.0 km/degree of latitude everywhere,
        # scaled by cos(latitude) for longitude.
        lat, lon, radius_km = near_km
        lat_delta = radius_km / 111.0
        lon_delta = radius_km / (111.0 * max(math.cos(math.radians(lat)), 1e-6))
        stmt = stmt.where(
            asset_exif.c.latitude.between(lat - lat_delta, lat + lat_delta),
            asset_exif.c.longitude.between(lon - lon_delta, lon + lon_delta),
        )

    if ids is not None:
        stmt = stmt.where(asset.c.id.in_(ids))
    if exclude_ids:
        stmt = stmt.where(asset.c.id.notin_(exclude_ids))

    with engine.connect() as conn:
        if randomize and user_id is not None:
            rows = _sample_mixed(conn, stmt, user_id, limit)
        elif randomize:
            # Pivot on asset.id (see ._random's docstring for why that's safe) instead of
            # `ORDER BY random()`, which would force a full scan+sort of every matching asset.
            rows = sample_by_id_pivot(conn, stmt, asset.c.id, limit)
        else:
            rows = conn.execute(stmt.order_by(asset.c.fileCreatedAt).limit(limit)).all()

    return [row_to_asset(row) for row in rows]


def get_distinct_locations(engine: Engine, user_id: UUID | None, field: LocationField) -> list[str]:
    """Every distinct non-null value of `field` among visible assets - powers Trivium's
    location_country/location_city distractors, which have to be real places that actually appear
    somewhere in the library, never invented strings. Not restricted to assets with a thumbnail
    (unlike get_assets) - these values are only ever borrowed as distractor text, never shown as
    the photo itself, so a thumbnail-less asset's location is still a perfectly good distractor."""
    column = asset_exif.c.city if field == "city" else asset_exif.c.country
    stmt = (
        select(column)
        .distinct()
        .select_from(asset.join(asset_exif, asset_exif.c.assetId == asset.c.id))
        .where(
            asset.c.status == "active",
            asset.c.visibility == "timeline",
            asset.c.deletedAt.is_(None),
            visible_asset(user_id),
            column.is_not(None),
        )
    )
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(stmt).all()]

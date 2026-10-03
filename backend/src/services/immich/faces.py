"""Postgres queries over Immich's `asset_face` table, joined with `person` for named faces."""

from uuid import UUID

from sqlalchemy import FromClause, Select, distinct, exists, select
from sqlalchemy.engine import Engine

from domain.face import Face
from persistence.immich_tables import asset, asset_face

from ._random import sample_by_id_pivot
from ._rows import row_to_face
from ._scope import person_rows, visible_asset

_visible_face = asset_face.c.isVisible.is_(True) & asset_face.c.deletedAt.is_(None)


def _named_face(p: FromClause) -> FromClause:
    return asset_face.join(p, p.c.personGroupId == asset_face.c.personGroupId)


def _eligible_asset_id_stmt(
    user_id: UUID | None, exclude_asset_ids: frozenset[UUID], exclude_person_ids: frozenset[UUID]
) -> Select[tuple[UUID]]:
    """Every asset with at least one visible, non-deleted face already assigned to a named,
    non-hidden person - shared by get_random_asset_with_named_faces (which samples one) and
    has_named_faces_asset (which only checks whether the set is non-empty). Hidden people
    (Immich's own `isHidden` flag) are excluded the same way get_persons/search_persons already
    exclude them from the guess search box - otherwise a round could black out a face the player
    has no way to search for and guess. `exclude_person_ids` follows the same reasoning but
    caller-driven (services/reports_service.py's open-report exclusion)."""
    p = person_rows(user_id)
    stmt = (
        select(asset.c.id)
        .select_from(asset.join(_named_face(p), asset_face.c.assetId == asset.c.id))
        .where(
            asset.c.status == "active",
            asset.c.visibility == "timeline",
            asset.c.deletedAt.is_(None),
            visible_asset(user_id),
            _visible_face,
            p.c.name != "",
            p.c.isHidden.is_(False),
        )
    )
    if exclude_asset_ids:
        stmt = stmt.where(asset.c.id.notin_(exclude_asset_ids))
    if exclude_person_ids:
        stmt = stmt.where(p.c.personGroupId.notin_(exclude_person_ids))
    return stmt


def has_named_faces_asset(
    engine: Engine,
    user_id: UUID | None,
    *,
    exclude_asset_ids: frozenset[UUID] = frozenset(),
    exclude_person_ids: frozenset[UUID] = frozenset(),
) -> bool:
    """Cheap existence check for get_random_asset_with_named_faces' pool - a plain `SELECT 1 ...
    LIMIT 1` over the same eligibility join, no id-pivot sampling and no second (per-asset faces)
    query. Powers games/whos_that_person/content.py::LiveContent.has_more, which only needs to
    know *whether* another round is possible, not which asset it would be."""
    stmt = select(exists(_eligible_asset_id_stmt(user_id, exclude_asset_ids, exclude_person_ids)))
    with engine.connect() as conn:
        return bool(conn.execute(stmt).scalar())


def get_random_asset_with_named_faces(
    engine: Engine,
    user_id: UUID | None,
    *,
    exclude_asset_ids: frozenset[UUID] = frozenset(),
    exclude_person_ids: frozenset[UUID] = frozenset(),
) -> list[Face]:
    """Picks one random asset that has at least one visible, non-deleted face already assigned
    to a named, non-hidden person, then returns every one of that asset's named, non-hidden
    faces - which of them to actually black out for a Who'sThatPerson round is that game's own
    decision (games/whos_that_person/content.py::LiveContent.pick_round), not this query's.
    Faces without a name are never returned -
    there'd be nothing to grade against, so they're left unblacked in the photo, purely
    decorative. Empty list if no eligible asset exists (e.g. exclude_asset_ids/exclude_person_ids/
    the game's data pool is exhausted)."""
    asset_id_stmt = _eligible_asset_id_stmt(user_id, exclude_asset_ids, exclude_person_ids)
    # GROUP BY (not DISTINCT) - the join produces one row per matching face, so this collapses
    # back to one row per asset before picking.
    asset_id_stmt = asset_id_stmt.group_by(asset.c.id)

    with engine.connect() as conn:
        # Pivot on asset.id (see services/immich/_random.py) instead of `ORDER BY random()`,
        # which would force a full scan+sort of every asset with at least one named face.
        asset_rows = sample_by_id_pivot(conn, asset_id_stmt, asset.c.id, 1)
        if not asset_rows:
            return []
        asset_row = asset_rows[0]

        # No SQL-level LIMIT here - every eligible face is fetched so the count to actually
        # hide can be decided in Python below (this asset's face count is at most a handful,
        # never a performance concern).
        p = person_rows(user_id)
        faces_stmt = (
            select(
                asset_face.c.id,
                asset_face.c.assetId,
                asset_face.c.personGroupId,
                p.c.name,
                asset_face.c.imageWidth,
                asset_face.c.imageHeight,
                asset_face.c.boundingBoxX1,
                asset_face.c.boundingBoxY1,
                asset_face.c.boundingBoxX2,
                asset_face.c.boundingBoxY2,
            )
            .select_from(_named_face(p))
            .where(
                asset_face.c.assetId == asset_row.id,
                _visible_face,
                p.c.name != "",
                p.c.isHidden.is_(False),
            )
        )
        if exclude_person_ids:
            faces_stmt = faces_stmt.where(p.c.personGroupId.notin_(exclude_person_ids))
        face_rows = conn.execute(faces_stmt).all()

    return [row_to_face(row) for row in face_rows]


def get_named_persons_in_asset(engine: Engine, user_id: UUID | None, asset_id: UUID) -> list[str]:
    """Names of every named, non-hidden, visible person tagged in this specific asset, ordered by
    name - same eligibility filters as get_random_asset_with_named_faces (isVisible/not deleted,
    named, not hidden), just scoped to a given asset instead of picking one at random. Powers the
    report modal's "who's in this photo" context - not part of round generation."""
    p = person_rows(user_id)
    stmt = (
        select(distinct(p.c.name))
        .select_from(_named_face(p))
        .where(
            asset_face.c.assetId == asset_id,
            _visible_face,
            p.c.name != "",
            p.c.isHidden.is_(False),
        )
        .order_by(p.c.name)
    )
    with engine.connect() as conn:
        return list(conn.scalars(stmt))

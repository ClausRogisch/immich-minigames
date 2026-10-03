"""Postgres queries over Immich's `person`/`asset_face` tables. A person's id is its Immich 3
`personGroupId` (see persistence/immich_tables.py's `person`), and every query is scoped to one
Immich user via ._scope."""

from datetime import date
from uuid import UUID

from sqlalchemy import ColumnElement, Date, FromClause, cast, func, select
from sqlalchemy.engine import Engine

from domain.person import Person
from persistence.immich_tables import asset, asset_face

from ._rows import row_to_person
from ._scope import person_rows, visible_asset
from ._text import word_prefix_conditions


def _visible_face(user_id: UUID | None) -> ColumnElement[bool]:
    return asset_face.c.deletedAt.is_(None) & asset_face.c.isVisible.is_(True) & visible_asset(user_id)


def _with_asset_counts(p: FromClause, user_id: UUID | None) -> FromClause:
    """`p` left-joined to its faces on assets this user can see - the asset_count every person
    query reports only counts photos the player could actually be shown."""
    faces = asset_face.join(asset, asset.c.id == asset_face.c.assetId)
    return p.outerjoin(faces, (asset_face.c.personGroupId == p.c.personGroupId) & _visible_face(user_id))


def get_persons(
    engine: Engine,
    user_id: UUID | None,
    *,
    named_only: bool = True,
    with_birthdate: bool | None = None,
    min_asset_count: int | None = None,
    name_query: str | None = None,
    ids: frozenset[UUID] | None = None,
    randomize: bool = False,
    asset_count_weight: float | None = None,
    limit: int = 1,
    exclude_ids: frozenset[UUID] = frozenset(),
) -> list[Person]:
    p = person_rows(user_id)
    asset_count_agg = func.count(func.distinct(asset_face.c.assetId))
    asset_count = asset_count_agg.label("asset_count")

    stmt = (
        select(p.c.personGroupId.label("id"), p.c.name, p.c.birthDate, asset_count)
        .select_from(_with_asset_counts(p, user_id))
        .where(p.c.isHidden.is_(False), p.c.thumbnailPath != "")
        .group_by(p.c.personGroupId, p.c.name, p.c.birthDate)
    )

    if named_only:
        stmt = stmt.where(p.c.name != "")
    if with_birthdate is True:
        stmt = stmt.where(p.c.birthDate.is_not(None))
    elif with_birthdate is False:
        stmt = stmt.where(p.c.birthDate.is_(None))
    if name_query:
        stmt = stmt.where(p.c.name.ilike(f"%{name_query}%"))
    if ids is not None:
        stmt = stmt.where(p.c.personGroupId.in_(ids))
    if exclude_ids:
        stmt = stmt.where(p.c.personGroupId.notin_(exclude_ids))
    if min_asset_count is not None:
        stmt = stmt.having(asset_count >= min_asset_count)

    if randomize and asset_count_weight:
        # Weighted random pick (Efraimidis-Spirakis: order by random()^(1/weight) desc)
        # instead of a plain ORDER BY random() - see games/immichdle.py's
        # ASSET_COUNT_WEIGHT_EXPONENT for the admin-configurable exponent this implements.
        # greatest(..., 1) avoids a division by zero for a person with 0 tagged assets.
        weight = func.pow(func.greatest(asset_count_agg, 1), asset_count_weight)
        stmt = stmt.order_by(func.pow(func.random(), 1.0 / weight).desc())
    elif randomize:
        stmt = stmt.order_by(func.random())
    else:
        stmt = stmt.order_by(p.c.name)
    stmt = stmt.limit(limit)

    with engine.connect() as conn:
        rows = conn.execute(stmt).all()

    return [row_to_person(row) for row in rows]


def get_persons_with_birthday_on(engine: Engine, user_id: UUID | None, month: int, day: int) -> list[Person]:
    """Every eligible named person (same isHidden/thumbnailPath/name/birthDate filters as
    get_persons' named_only+with_birthdate) whose birthDate falls on this month/day, any year -
    Web Push's daily birthday notification (services/notifications/content.py).

    29 February deliberately never matches outside a leap year - not worked around with a nearby
    date, since inventing "the 28th" would be wrong more years than it's right. Age itself
    (current_year - birth_year) is the caller's job, not this query's - it doesn't know "today"."""
    p = person_rows(user_id)
    asset_count_agg = func.count(func.distinct(asset_face.c.assetId))
    stmt = (
        select(p.c.personGroupId.label("id"), p.c.name, p.c.birthDate, asset_count_agg.label("asset_count"))
        .select_from(_with_asset_counts(p, user_id))
        .where(
            p.c.isHidden.is_(False),
            p.c.thumbnailPath != "",
            p.c.name != "",
            p.c.birthDate.is_not(None),
            func.extract("month", p.c.birthDate) == month,
            func.extract("day", p.c.birthDate) == day,
        )
        .group_by(p.c.personGroupId, p.c.name, p.c.birthDate)
        .order_by(p.c.name)
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    return [row_to_person(row) for row in rows]


def search_persons(
    engine: Engine, user_id: UUID | None, query: str, *, offset: int = 0, limit: int = 3
) -> list[Person]:
    """Named people matching every whitespace-separated token in `query` against a *word* in the
    name - see ._text.word_prefix_conditions for the actual matching rule. Kept separate from
    get_persons - its existing name_query is a plain substring filter, and nothing else needs this
    word-prefix mode. Paginated via offset/limit (small pages, e.g. for infinite scroll UIs),
    ordered by name for a stable scroll order."""
    p = person_rows(user_id)
    token_conditions = word_prefix_conditions(p.c.name, query)
    if not token_conditions:
        return []

    asset_count = func.count(func.distinct(asset_face.c.assetId)).label("asset_count")

    stmt = (
        select(p.c.personGroupId.label("id"), p.c.name, p.c.birthDate, asset_count)
        .select_from(_with_asset_counts(p, user_id))
        .where(
            p.c.isHidden.is_(False),
            p.c.thumbnailPath != "",
            p.c.name != "",
            *token_conditions,
        )
        .group_by(p.c.personGroupId, p.c.name, p.c.birthDate)
        .order_by(p.c.name)
        .offset(offset)
        .limit(limit)
    )

    with engine.connect() as conn:
        rows = conn.execute(stmt).all()

    return [row_to_person(row) for row in rows]


def get_person_first_asset_date(engine: Engine, user_id: UUID | None, person_id: UUID) -> date | None:
    """Local calendar day of this person's earliest tagged asset - same local-day expression
    get_assets uses (see that function's localDate comment). Powers Immichdle's FirstAppearance
    clue. None if the person has no visible, non-deleted face tags."""
    local_date = cast(func.timezone("UTC", asset.c.localDateTime), Date)
    stmt = (
        select(func.min(local_date))
        .select_from(asset_face.join(asset, asset.c.id == asset_face.c.assetId))
        .where(
            asset_face.c.personGroupId == person_id,
            _visible_face(user_id),
            asset.c.status == "active",
            asset.c.visibility == "timeline",
            asset.c.deletedAt.is_(None),
        )
    )
    with engine.connect() as conn:
        return conn.execute(stmt).scalar()


def get_assets_together_count(engine: Engine, user_id: UUID | None, person_a_id: UUID, person_b_id: UUID) -> int:
    """How many distinct assets (that this user can see) have both people face-tagged - powers
    Immichdle's AssetsTogether clue."""
    face_a = asset_face.alias("face_a")
    face_b = asset_face.alias("face_b")
    stmt = (
        select(func.count(func.distinct(face_a.c.assetId)))
        .select_from(
            face_a.join(face_b, face_a.c.assetId == face_b.c.assetId).join(asset, asset.c.id == face_a.c.assetId)
        )
        .where(
            visible_asset(user_id),
            face_a.c.personGroupId == person_a_id,
            face_a.c.deletedAt.is_(None),
            face_a.c.isVisible.is_(True),
            face_b.c.personGroupId == person_b_id,
            face_b.c.deletedAt.is_(None),
            face_b.c.isVisible.is_(True),
        )
    )
    with engine.connect() as conn:
        return conn.execute(stmt).scalar() or 0


def get_top_co_occurring_persons(
    engine: Engine,
    user_id: UUID | None,
    person_id: UUID,
    *,
    limit: int = 3,
    exclude_ids: frozenset[UUID] = frozenset(),
) -> list[tuple[UUID, str, int]]:
    """Named people ranked by how many assets they share a face tag with `person_id` in, most
    co-occurrences first - (person_id, name, count) tuples. Powers Trivium's photos_together
    question type, which needs to *rank* candidates in one query rather than call
    get_assets_together_count once per candidate (N pairwise queries)."""
    p = person_rows(user_id)
    face_a = asset_face.alias("face_a")
    face_b = asset_face.alias("face_b")
    count = func.count(func.distinct(face_a.c.assetId)).label("together_count")
    stmt = (
        select(face_b.c.personGroupId, p.c.name, count)
        .select_from(
            face_a.join(face_b, face_a.c.assetId == face_b.c.assetId)
            .join(asset, asset.c.id == face_a.c.assetId)
            .join(p, p.c.personGroupId == face_b.c.personGroupId)
        )
        .where(
            visible_asset(user_id),
            face_a.c.personGroupId == person_id,
            face_a.c.deletedAt.is_(None),
            face_a.c.isVisible.is_(True),
            face_b.c.deletedAt.is_(None),
            face_b.c.isVisible.is_(True),
            face_b.c.personGroupId != person_id,
            p.c.isHidden.is_(False),
            p.c.name != "",
        )
    )
    if exclude_ids:
        stmt = stmt.where(face_b.c.personGroupId.notin_(exclude_ids))
    stmt = stmt.group_by(face_b.c.personGroupId, p.c.name).order_by(count.desc()).limit(limit)

    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    return [(row.personGroupId, row.name, row.together_count) for row in rows]

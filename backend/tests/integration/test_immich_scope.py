"""Per-player scoping (services/immich/_scope.py) against the real Immich DB: everything a scoped
ImmichService returns must be visible to that Immich user in Immich itself. Users are picked from
whatever the DB holds rather than hardcoded - the partner/shared-album tests skip if the DB has no
partner sharing / no album shared across users."""

from uuid import UUID

import pytest
from sqlalchemy import text

from services.immich import ImmichService


def _user_with_most_named_people(engine) -> UUID | None:
    with engine.connect() as conn:
        return conn.execute(
            text(
                """SELECT "ownerId" FROM person WHERE name != '' AND NOT "isHidden"
                   GROUP BY "ownerId" ORDER BY count(*) DESC LIMIT 1"""
            )
        ).scalar()


def _visible_owner_ids(engine, user_id: UUID) -> set[UUID]:
    with engine.connect() as conn:
        partners = conn.execute(
            text('SELECT "sharedById" FROM partner WHERE "sharedWithId" = :u'), {"u": user_id}
        ).scalars()
        return {user_id, *partners}


def _owners(engine, asset_ids) -> dict[UUID, tuple[UUID, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text('SELECT id, "ownerId", visibility::text AS visibility FROM asset WHERE id = ANY(:ids)'),
            {"ids": list(asset_ids)},
        )
        return {row.id: (row.ownerId, row.visibility) for row in rows}


def _in_member_album(engine, user_id: UUID, asset_ids) -> set[UUID]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    """SELECT aa."assetId" FROM album_asset aa
                       JOIN album_user au ON au."albumId" = aa."albumId"
                       WHERE au."userId" = :u AND aa."assetId" = ANY(:ids)"""
                ),
                {"u": user_id, "ids": list(asset_ids)},
            ).scalars()
        )


# An eligible, thumbnailed photo that a user only sees through an album shared with them - owned by
# someone who is neither them nor a partner sharing with them.
_SHARED_ONLY_PHOTO = """
    SELECT au."userId" AS member, a.id AS asset_id, a."ownerId" AS owner
    FROM album_user au
    JOIN album_asset aa ON aa."albumId" = au."albumId"
    JOIN asset a ON a.id = aa."assetId"
    WHERE a."ownerId" <> au."userId"
      AND a."ownerId" NOT IN (SELECT "sharedById" FROM partner WHERE "sharedWithId" = au."userId")
      AND a.status = 'active' AND a.visibility = 'timeline' AND a."deletedAt" IS NULL
      AND EXISTS (SELECT 1 FROM asset_file f WHERE f."assetId" = a.id AND f.type = 'thumbnail')
    LIMIT 1
"""


@pytest.fixture
def scoped_user(immich_engine) -> UUID:
    user_id = _user_with_most_named_people(immich_engine)
    if user_id is None:
        pytest.skip("no Immich user with named people in this DB")
    return user_id


class TestScopedQueries:
    def test_assets_are_own_partners_timeline_or_in_a_member_album(self, immich_engine, scoped_user):
        service = ImmichService(immich_engine, user_id=scoped_user)
        allowed = _visible_owner_ids(immich_engine, scoped_user)

        assets = service.get_assets(randomize=True, limit=200)
        faces = service.get_random_asset_with_named_faces()

        owners = _owners(immich_engine, {a.id for a in assets} | {f.asset_id for f in faces})
        assert owners
        via_albums = _in_member_album(immich_engine, scoped_user, owners)
        for asset_id, (owner_id, visibility) in owners.items():
            home = owner_id == scoped_user or (owner_id in allowed and visibility == "timeline")
            assert home or asset_id in via_albums

    def test_people_are_the_users_own_rows_without_duplicates(self, immich_engine, scoped_user):
        service = ImmichService(immich_engine, user_id=scoped_user)

        persons = service.get_persons(randomize=True, limit=100)

        assert persons
        assert len({p.id for p in persons}) == len(persons)
        with immich_engine.connect() as conn:
            owned = conn.execute(
                text('SELECT count(*) FROM person WHERE "ownerId" = :u AND "personGroupId" = ANY(:ids)'),
                {"u": scoped_user, "ids": [p.id for p in persons]},
            ).scalar()
        assert owned == len(persons)

    def test_albums_are_only_ones_the_user_is_a_member_of(self, immich_engine, scoped_user):
        service = ImmichService(immich_engine, user_id=scoped_user)

        albums = service.get_albums(randomize=True, limit=100)

        if not albums:
            pytest.skip("scoped user has no albums")
        with immich_engine.connect() as conn:
            members = conn.execute(
                text('SELECT count(DISTINCT "albumId") FROM album_user WHERE "userId" = :u AND "albumId" = ANY(:ids)'),
                {"u": scoped_user, "ids": [a.id for a in albums]},
            ).scalar()
        assert members == len(albums)

    def test_a_user_with_nothing_sees_nothing(self, immich_engine):
        service = ImmichService(immich_engine, user_id=UUID(int=0))

        assert service.get_assets(randomize=True, limit=5) == []
        assert service.get_persons(limit=5) == []
        assert service.get_albums(limit=5) == []
        assert service.has_named_faces_asset() is False

    def test_unscoped_returns_one_row_per_person(self, immich_engine):
        persons = ImmichService(immich_engine).get_persons(named_only=False, limit=10_000)

        assert len({p.id for p in persons}) == len(persons)


class TestPartnerSharing:
    def test_partner_timeline_assets_are_included(self, immich_engine):
        with immich_engine.connect() as conn:
            pair = conn.execute(text('SELECT "sharedById", "sharedWithId" FROM partner LIMIT 1')).first()
        if pair is None:
            pytest.skip("no partner sharing in this DB")
        shared_by, shared_with = pair

        assets = ImmichService(immich_engine, user_id=shared_with).get_assets(randomize=True, limit=500)

        owners = {owner for owner, _ in _owners(immich_engine, {a.id for a in assets}).values()}
        assert shared_by in owners


class TestSharedAlbums:
    def test_a_photo_only_shared_through_an_album_is_visible_to_the_member(self, immich_engine):
        with immich_engine.connect() as conn:
            row = conn.execute(text(_SHARED_ONLY_PHOTO)).first()
        if row is None:
            pytest.skip("no album shared across users in this DB")

        found = ImmichService(immich_engine, user_id=row.member).get_assets(ids=frozenset({row.asset_id}))

        assert [a.id for a in found] == [row.asset_id]

    def test_but_not_to_an_unrelated_user(self, immich_engine):
        with immich_engine.connect() as conn:
            row = conn.execute(text(_SHARED_ONLY_PHOTO)).first()
        if row is None:
            pytest.skip("no album shared across users in this DB")

        assert ImmichService(immich_engine, user_id=UUID(int=0)).get_assets(ids=frozenset({row.asset_id})) == []

    def test_people_rounds_only_name_faces_in_the_players_own_clusters(self, immich_engine, scoped_user):
        faces = ImmichService(immich_engine, user_id=scoped_user).get_random_asset_with_named_faces()

        if not faces:
            pytest.skip("scoped user has no face round")
        with immich_engine.connect() as conn:
            own = conn.execute(
                text('SELECT count(*) FROM person WHERE "ownerId" = :u AND "personGroupId" = ANY(:ids)'),
                {"u": scoped_user, "ids": list({f.person_id for f in faces})},
            ).scalar()
        assert own == len({f.person_id for f in faces})

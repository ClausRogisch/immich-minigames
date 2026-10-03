"""What one Immich user can see - the per-player scoping every query in this package applies.

`user_id` is the Immich user id of the player (persistence/users.py's UserModel.immich_user_id),
or None for an unscoped, installation-wide read (admin views, background ML jobs). Scoping mirrors
what that user sees in Immich itself:

- Assets, in two parts:
  - "home" (home_asset): their own (anything but the PIN-locked folder), plus their partners'
    timeline assets. Partner visibility doesn't depend on `partner.inTimeline` - that flag only
    controls whether the partner's photos are mixed into the user's own timeline, they're viewable
    in Immich either way. External-library assets are ordinary `asset` rows owned by the library's
    owner, so they're covered by the same rule with no special case.
  - "shared album" (shared_album_asset): anything in an album the user is a member of, whoever
    owns it - Immich shows album members every asset in the album.
  visible_asset is the union. Photo-only sampling caps how much of a game the shared part may
  make up (see assets.py's get_assets), so one big shared album can't crowd out the user's own.
- People: the user's own `person` row for each face cluster (their names, hidden flags and birth
  dates - two users can name the same cluster differently). Unscoped reads pick one row per
  cluster, preferring a named, non-hidden one. Faces only ever resolve through these rows, so a
  shared-album photo of someone else's only takes part in people games through faces in clusters
  the user has a row for (e.g. after joining the owner's face cluster group in Immich) - faces from
  clusters the user doesn't have are never named, counted or asked about.
- Albums: every album the user is a member of (owner or shared with them, `album_user`).
"""

from uuid import UUID

from sqlalchemy import ColumnElement, FromClause, and_, exists, or_, select, true

from persistence.immich_tables import album, album_asset, album_user, asset, partner, person


def home_asset(user_id: UUID) -> ColumnElement[bool]:
    """Predicate over `asset`: the user's own assets plus their partners' - see the module docstring."""
    partner_owner_ids = select(partner.c.sharedById).where(partner.c.sharedWithId == user_id)
    return or_(
        and_(asset.c.ownerId == user_id, asset.c.visibility != "locked"),
        and_(asset.c.ownerId.in_(partner_owner_ids), asset.c.visibility == "timeline"),
    )


def shared_album_asset(user_id: UUID) -> ColumnElement[bool]:
    """Predicate over `asset`: in a (non-deleted) album the user is a member of - see the module
    docstring. Overlaps home_asset for the user's own photos in their own albums."""
    in_member_album = exists(
        select(1)
        .select_from(
            album_asset.join(album_user, album_user.c.albumId == album_asset.c.albumId).join(
                album, album.c.id == album_asset.c.albumId
            )
        )
        .where(album_asset.c.assetId == asset.c.id, album_user.c.userId == user_id, album.c.deletedAt.is_(None))
    )
    return and_(in_member_album, asset.c.visibility != "locked")


def visible_asset(user_id: UUID | None) -> ColumnElement[bool]:
    """Predicate over `asset`: everything the user can see - see the module docstring. Always true
    when unscoped."""
    if user_id is None:
        return true()
    return or_(home_asset(user_id), shared_album_asset(user_id))


def visible_album(user_id: UUID | None) -> ColumnElement[bool]:
    """Predicate over `album` - see the module docstring. Always true when unscoped."""
    if user_id is None:
        return true()
    return exists(select(1).where(album_user.c.albumId == album.c.id, album_user.c.userId == user_id))


def person_rows(user_id: UUID | None) -> FromClause:
    """`person` reduced to at most one row per person_group (the Immich 3 person id) - the user's
    own row when scoped, see the module docstring. Exposes the same columns as `person`, so callers
    read e.g. `p.c.personGroupId`/`p.c.name` off the returned subquery instead of the table."""
    if user_id is not None:
        return select(person).where(person.c.ownerId == user_id).subquery("person")
    return (
        select(person)
        .distinct(person.c.personGroupId)
        # Booleans sort false first: a named, non-hidden row wins over an unnamed/hidden one.
        .order_by(person.c.personGroupId, (person.c.name == "").asc(), person.c.isHidden.asc(), person.c.ownerId)
        .subquery("person")
    )

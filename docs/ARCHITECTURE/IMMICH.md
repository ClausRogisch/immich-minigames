# Immich integration

How this app reads from an existing Immich deployment. Everything here is derived from
`backend/src/persistence/immich_tables.py`, `backend/src/services/immich_service.py`,
`backend/src/services/ml_service.py` and `backend/src/scripts/bootstrap_db_role.py` — those files
are the ground truth; this doc explains the *why*.

## Two ways to talk to Immich

Immich is reached through **two separate channels**, and which one to use is not a style choice:

| | Postgres (direct SQL) | Immich REST API |
|---|---|---|
| Used for | All game metadata: assets, people, faces, dates, GPS, face embeddings | Image bytes only |
| Why | Immich's API has no endpoint for "a random photo that has GPS and a thumbnail, excluding these 12 ids" — that is a query, and expressing it as one is far cheaper than paging the API | The image files live on disk, not in Postgres. The DB only stores paths, which this app has no filesystem access to |
| Auth | `DB_APP_USERNAME` / `DB_APP_PASSWORD` (scoped role, below) | The **player's own** Immich API key (`x-api-key`), see [Per-player scoping](#per-player-scoping) |
| Code | `ImmichService.get_assets` / `get_persons` / `search_persons` / … , `MLService` | `ImmichService.get_asset_thumbnail` / `get_person_thumbnail` |

**Never fetch image bytes via SQL, and never fetch metadata via the REST API.** Mixing the two is
the single easiest way to make this app slow or brittle.

### Thumbnails

Both thumbnail methods are thin proxies over Immich, returning `(bytes, content-type)` and letting
`httpx` errors propagate for `api/api.py::_proxy_thumbnail` to map:

- `GET {IMMICH_SERVER_URL}/api/assets/{id}/thumbnail?size=preview` — `size=preview` (~1440px JPEG)
  is deliberate: `thumbnail` (~250px) is too small for a fullscreen game, and `original` may be
  HEIC/RAW/video, which a browser `<img>` cannot render.
- `GET {IMMICH_SERVER_URL}/api/people/{id}/thumbnail` — the face crop.

One pooled `httpx.Client` with a 10s timeout is shared process-wide (`_get_http_client`,
`lru_cache`d) so a slow Immich cannot exhaust worker threads.

Both are fetched with the requesting player's own key, so Immich itself refuses anything that
player can't see: a 403 is mapped to a plain 404 (the frontend shows its placeholder), and a 401
(revoked key) to the same 409 `immich_not_linked` an unlinked player gets.

> ⚠️ Within their own library, a player can still open an asset's thumbnail URL directly - in
> Who'sThatPerson that bypasses the blacked-out faces.

## The scoped DB role

The backend never uses Immich's admin Postgres credentials. `scripts/bootstrap_db_role.py` (run
once as the `db-init` compose service) provisions a dedicated role:

- **Immich's database: `SELECT` only**, granted via `GRANT pg_read_all_data`. Role membership
  rather than explicit `GRANT`s on `public`, for two reasons: it lives in `pg_auth_members` at the
  cluster level, so Immich's own database keeps zero references to this app in its dumps; and it
  doesn't go stale when an Immich upgrade adds a table.
- **This app's own database: full control** inside its `minigames` schema — the app creates and
  migrates its own tables there. A different database, so no query can span the two.
- `ALTER ROLE … IN DATABASE <immich> SET default_transaction_read_only = on` — a guard rail against
  programming mistakes, **not** a security boundary: the role can still `SET TRANSACTION READ
  WRITE` on itself. The real boundary is that only `SELECT` is ever granted.
- `REVOKE CREATE ON SCHEMA public FROM PUBLIC` — needed because Postgres 14 and earlier grant
  `CREATE` on `public` to everyone by default. Note this is a change to *Immich's* schema ACL
  affecting every role, not just ours. It names no role of ours, so unlike an
  explicit `GRANT` it restores cleanly anywhere.

Consequence for anyone writing queries: **you cannot `CREATE EXTENSION`, and you cannot write
anything to Immich's database at all.** This is why accent-insensitive search uses the builtin
`translate()` rather than `unaccent` (see below).

## Tables read

Only the columns actually used are declared, as SQLAlchemy Core `Table()`s (not ORM models — these
are Immich's tables, migrated by Immich).

**`asset`** — `id`, `ownerId`, `type` (`IMAGE`/`VIDEO`), `fileCreatedAt`, `localDateTime`,
`originalFileName`, `stackId`, `visibility`, `status`, `isFavorite`, `width`, `height`, `deletedAt`.

**`asset_exif`** — `assetId`, `latitude`, `longitude`, `city`, `state`, `country`.

**`asset_file`** — `id`, `assetId`, `type`. Used only to prove a thumbnail exists
(`type = 'thumbnail'`) before a game shows an asset.

**`person`** — `ownerId`, `personGroupId` (together the primary key), `name`, `birthDate`,
`thumbnailPath`, `isHidden`. Since Immich 3 there is no `person.id`: the shared face cluster is a
`person_group`, and its id is what Immich's API (`/people/{id}/...`) and every `asset_face` call a
person's id. `person` is one row per (Immich user, cluster) with *that user's* name/birth date/hidden
flag, so reads must pick one user's row (`services/immich/_scope.py`'s `person_rows`). Immich 3's
upgrade reused each old `person.id` as its `person_group` id, so ids stored before then still
resolve.

**`asset_face`** — `id`, `assetId`, `personGroupId` (was `personId` before Immich 3), `isVisible`,
`deletedAt`, plus the detection box: `imageWidth`, `imageHeight`, `boundingBoxX1/Y1/X2/Y2`.

**`album_user`** — `albumId`, `userId`. Every album member, owner included (Immich 3 dropped
`album.ownerId`).

**`partner`** — `sharedById`, `sharedWithId`. Partner sharing.

**`face_search`** — not declared as a `Table()`; queried via raw SQL in `MLService` because of the
pgvector operator. Holds `faceId` and `embedding vector(512)`.

### Two gotchas that are easy to get wrong

**1. Native enum types.** `asset.status` and `asset.visibility` are real Postgres enums, not
varchars. They must be declared as `ENUM(..., create_type=False)`, otherwise comparing them to a
bound string fails with `operator does not exist: assets_status_enum = character varying`.

**2. `localDateTime` is not a normal timestamptz.** Immich stores the device-local wall clock in it
as a timestamptz whose *UTC rendering* is the local time (a 21:55 local shot is stored `21:55+00`).
So the true local calendar day is `CAST(timezone('UTC', "localDateTime") AS date)` — which is what
`get_assets` computes as `localDate`. Using `fileCreatedAt::date` instead silently shifts photos
taken late in the evening to the next day, which would make a correct Dateguessr guess score as
off-by-one. `domain/asset.py` exposes this as `local_date`, and Dateguessr grades against it.

### The standard eligibility filter

Every asset query starts from the same "this asset is real and showable" predicate:

```sql
status = 'active' AND visibility = 'timeline' AND "deletedAt" IS NULL
AND EXISTS (SELECT 1 FROM asset_file WHERE "assetId" = asset.id AND type = 'thumbnail')
```

And every person query filters `isHidden = false AND "thumbnailPath" <> ''`, plus `name <> ''`
whenever `named_only` (the default). Faces additionally require `isVisible AND "deletedAt" IS NULL`.
Hidden people are excluded everywhere *consistently* — a Who'sThatPerson round must never black out
a face the player cannot find in the search box.

`get_random_asset_with_named_faces` (`services/immich/faces.py`) also takes an optional
`exclude_person_ids` (roadmap #N, the metadata-reporting feature's round-generation exclusion —
see `docs/ARCHITECTURE/BACKEND.md`), applied to both of its statements the same way `isHidden` is:
the one that picks the candidate asset (so an asset whose only named face belongs to an excluded
person is never picked) and the one that fetches that asset's named faces (so an excluded person's
face never appears among an otherwise-eligible asset's candidates, even if a co-appearing person on
the same photo isn't excluded).

## Per-player scoping

Players don't share a library: each links their own Immich account (`users.immich_user_id` plus
an encrypted API key, see [docs/IMMICH_API_KEY.md](../IMMICH_API_KEY.md)), and `api/deps.py`'s
`get_immich_service` builds a request-scoped `ImmichService(user_id=..., api_key=...)` for them.
Every query in `services/immich/` applies the helpers in `services/immich/_scope.py`, which mirror
what that user sees in Immich:

| | Visible to user U |
|---|---|
| Assets | `ownerId = U` (except the locked folder), or owned by a partner sharing with U and on their timeline. External libraries are ordinary assets owned by the library owner. |
| People | U's own `person` row per cluster. Asset counts only count faces on assets U can see. |
| Albums | Any album U is an `album_user` of. Their assets aren't further filtered, since an album member sees all of them. |

`user_id=None` is unscoped (one `person` row per cluster, preferring a named, visible one). It's
only for admin views (`get_admin_immich_service`) and the ML embedding cache, which is per face
cluster, not per player. A player without a linked account gets `ImmichNotLinkedError` → 409
`immich_not_linked` from every route that needs Immich.

Daily challenges follow the same split: one `daily_challenges` row per (day, game_type, mode,
user), generated from that player's own library. The daily leaderboard ranks all of a day's
challenges for a mode together.

## Query techniques worth knowing

**Random selection** — `ORDER BY random() LIMIT n`. Note `SELECT DISTINCT … ORDER BY random()` is
rejected by Postgres (ORDER BY expressions must appear in the select list under DISTINCT), which is
why `get_persons` and `get_random_asset_with_named_faces` use `GROUP BY` instead of `DISTINCT`.

**Geographic prefilter** — `near_km` is a lat/lon **bounding box**, not a circle: `lat ± r/111`,
`lon ± r/(111·cos(lat))`. Callers needing a true radius (Geoguessr's 500m extras rule) run an exact
haversine filter in Python afterwards. The box is just to keep the DB from scanning everything.

**Accent-insensitive search** — `search_persons` folds accents with the builtin
`translate(name, 'áéíóú…', 'aeiou…')` on both sides rather than the `unaccent` extension, because
the scoped role cannot `CREATE EXTENSION`. Matching is per-token *word-prefix*: each whitespace
token must satisfy `folded ILIKE 'tok%' OR folded ILIKE '% tok%'`, all ANDed. So "rai rodriguez"
matches "Raimundo Rodríguez" regardless of typed order, but not mid-word. User input is escaped for
LIKE wildcards first (`_escape_like`).

> Note: `get_persons`'s own `name_query` is a plain substring `ILIKE` that does **not** escape
> wildcards — an inconsistency with `search_persons`.

## Face similarity (Immich-ML)

The `MLSimilarity` clue in Immichdle does **not** call the `immich-machine-learning` service. It
reads the embeddings that service already computed and stored in `face_search.embedding`
(`vector(512)`, pgvector). Two reasons: the ML container is not reachable from the host in the dev
stack, and recomputing an embedding for a face Immich already indexed is pure waste.

`MLService.face_similarity` compares each person's *representative* embedding — the element-wise
average (pgvector's `avg(vector)` aggregate) across all of that person's currently visible,
non-deleted faces, not a single photo — via plain cosine similarity (`1 - (a <=> b)`, pgvector's
`<=>` being cosine **distance**, `0..2`, so similarity is `-1..1`). Unrelated people typically land
close to `0`, not exactly at it: small negative values (e.g. -0.03) are normal and just mean "no
relationship", not a computation error. The backend keeps sending this signed value as-is; the
frontend's `mlSimilarityClue` (`clueColors.ts`) floors it at 0% for display only, and colors the clue
amber above 0.30 raw similarity - both are presentation-only decisions, not a change to what's
computed or stored here.

This went through three designs, in order:
1. `MAX(similarity)` over the full cross join of both people's faces — most accurate (a single
   off-angle photo can't undersell a real resemblance), but `O(n·m)` and slow enough with people
   who have hundreds/thousands of tagged photos to freeze a request.
2. A single representative face each (`person.faceAssetId`, the same photo Immich shows as that
   person's thumbnail, matching immich-power-tools' similar-faces query) — `O(1)`, but with ~300
   named people in practice two people rarely *look* alike going by one photo each; the clue read
   as noise more often than not.
3. **Current**: the averaged-embedding cache below — a `O(1)` amortized lookup (one query per
   person on a cache hit) with a more robust per-person representation than a single photo,
   without paying the cross-join cost on every comparison.

**The cache**: `minigames.person_face_embedding_cache` (`persistence/ml_cache.py`) — `person_id`
(PK), `embedding vector(512)`, `face_count`, `embedding_count`, `computed_at`. Lives in this app's
own database, not Immich's: Immich's database is read-only for this app's DB role (see "The scoped
DB role" above), so a cache this app writes to has nowhere to go but its own database, even though
the embeddings it's built from are read from Immich's `face_search`. This is also why the `vector`
extension now has to be installed in **both** databases — `scripts/bootstrap_db_role.py` runs
`CREATE EXTENSION IF NOT EXISTS vector` in the app's own database too now (Immich's already had it,
for `face_search`/`smart_search`).

**Freshness** is deliberately cheap, not exact: a cached row is considered stale (and recomputed)
whenever `face_count` no longer matches that person's current count of visible, non-deleted
`asset_face` rows. Swapping one face for another without changing the total count is not detected
- accepted imprecision, and part of why "reprocess all" (the admin embedding worker, roadmap #15,
below) exists: a merge/split can change *which* faces belong to a person without changing how many,
leaving a cached embedding silently wrong until someone forces a full recompute.

**Incremental update** (roadmap #15, migration `0013`): a stale row isn't always fully recomputed.
`person_face_embedding_cache.embedding_count` tracks how many vectors actually went into the cached
average - a different number from `face_count`'s "something changed" fingerprint - and `computed_at`
(`timestamptz`) is the watermark that average is valid *as of*, taken from Immich's own `now()`
before any vector is read (not the app database's clock - the two are compared across different
Postgres instances, so a naive timestamp or the wrong server's clock would have no defined meaning).
When a row goes stale, `MLService` first checks whether every currently-visible face that existed as
of that watermark (`asset_face.updatedAt <= computed_at`) still adds up to `embedding_count`; if so,
only the faces added since need averaging, and their average is folded into the cached one with a
count-weighted mean (`merge_weighted_average`, `services/ml_service.py`) - mathematically exact, not
an approximation, since the mean of a union of two disjoint sets equals the count-weighted mean of
their own means.

Both directions the watermark could be wrong resolve to a full recompute, never a wrong result: a
face that really was part of the old average but looks "new" (watermark too early), or one that
wasn't but looks "old" (watermark too late), both break the `embedding_count` match and fall back.
A face that was *edited* rather than added also moves its `updatedAt` forward, making it
indistinguishable from a removal here - same fallback, which is exactly what keeps it from being
folded in twice. `force=True` (`compute_person_embedding`/"reprocess all") always skips straight to
a full recompute - the only thing that resets the float rounding error a long chain of incremental
merges accumulates (negligible next to the clue's comparison thresholds, but non-zero).

## Album similarity (Albumdle, roadmap #14)

A sibling cache to the one above, for Albumdle's `Similarity` clue - but built from a **different**
table and a different kind of embedding entirely. Where `MLSimilarity` averages *face* embeddings
(`face_search`, one row per detected face) to compare two people, Albumdle's clue averages *CLIP*
embeddings (`smart_search`, one row per asset - Immich's semantic/text-search index, unrelated to
face detection) across every eligible asset in an album, to compare two albums as wholes. Confirmed
by the project owner explicitly: "tomar el CLIP de todos los assets de smart search... eso dará un
vector álbum promedio" - not a face-based signal at all.

`MLService.album_similarity` mirrors `face_similarity`'s shape exactly: plain cosine similarity
between two albums' averaged embeddings, same `-1..1` range and "small negative is normal" caveat.
The one query-level difference: `album_asset` rows outlive Immich's soft-delete (an asset's
`deletedAt`/`status` live on `asset`, not on the join table), so the averaging query joins through
`asset` and applies the standard eligibility filter - something the face-based query doesn't need,
since `asset_face` rows are pruned independently via their own `deletedAt`/`isVisible` columns.

**The cache**: `minigames.album_embedding_cache` (`persistence/album_ml_cache.py`) - `album_id` (PK),
`embedding vector(512)`, `asset_count`, `embedding_count`, `computed_at`. Same rationale for living
in this app's own database as the face cache above, reusing that module's hand-rolled `Vector`
SQLAlchemy type rather than duplicating it.

**Freshness** mirrors the face cache's own cheap-not-exact contract, substituting the album's current
raw `album_asset` row count for `face_count` as the staleness fingerprint.

**Incremental update**: same mechanism as the face cache above - `embedding_count`/`computed_at`
watermark, `merge_weighted_average` - substituting `album_asset` for `asset_face`. Unlike the face
cache, `album_embedding_cache.embedding_count` is nullable: it isn't the same number as
`asset_count` (the average only counts *eligible* assets with a `smart_search` row, `asset_count` is
every `album_asset` row), so a row from before migration `0013` has no way to know its true value -
`NULL` means exactly that, and forces one full recompute before the incremental path can ever apply
to that album. One gap the incremental update does **not** close, and isn't meant to: an asset can
become ineligible (archived, soft-deleted) without ever touching `album_asset` -
`deletedAt`/`status` live on `asset`, not on the join table. Neither the plain fingerprint nor the
incremental watermark notices that, so a removed asset's contribution lingers in the cached average
until "reprocess all" forces a full recompute - the same accepted imprecision the fingerprint always
had, not a regression the incremental path introduced.

## Dev instance

`docker-compose.yml` at the repo root stands up a dev Immich (server + ML + Postgres + Redis) to
develop against. It does **not** include this app's own backend/frontend — those are
`docker-compose.app.yml`.

The project owner runs this locally with real test data: photos uploaded, people recognized (some
named, some not, some with birthdays), assets spread across several distinct locations, and at
least one location cluster of 15-24 photos (which is why Geoguessr enforces a 50km minimum
separation between rounds). Exact counts are intentionally not recorded here — they drift with
every upload, and no code should depend on a specific number.

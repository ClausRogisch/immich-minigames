# Connecting your Immich account

Every player connects their **own** Immich account to the app with an API key. Games then only use
what that Immich account can see:

- your own photos and videos, including your **external libraries**
- photos your **partners** share with you (Immich → *Account Settings → Partner Sharing*), whether
  or not you show them in your own timeline
- **albums** you own or that are shared with you
- **people** as *you* named them in Immich. Since Immich 3, every user names face clusters
  independently, so a person you haven't named yourself won't show up in people-based games, even
  if a partner named them. Only named people you haven't hidden are used.

Photos in your Locked Folder are never used, and neither are your partners' archived photos
(Immich doesn't show you those either).

## 1. Create the key in Immich

In Immich: avatar (top right) → **Account Settings** → **API Keys** → **New API Key**.

- **Name:** anything, for example `minigames`.
- **Permissions:** choose custom permissions and tick only these three:

| Permission | Why the app needs it |
|---|---|
| `user.read` | Identifies which Immich user the key belongs to, so games use only that user's library |
| `asset.view` | Loads photo previews during games |
| `person.read` | Loads people's face thumbnails (Immichdle, More or Less, Trivium, the avatar picker) |

Leave everything else unticked. The app never uploads, downloads originals, edits or deletes
anything, so it doesn't need `asset.download`, `asset.upload` or any `*.create` / `*.update` /
`*.delete` permission. A key with **All** permissions also works, but isn't recommended.

## 2. Paste it into the app

In the app: **Profile → Immich connection**, paste the key and press **Connect**. New accounts are
sent there automatically, because nothing is playable until a key is connected.

The app checks the key against Immich before saving it. If something is wrong, it tells you what:

| Message | Fix |
|---|---|
| `Immich rejected this API key` | The key was mistyped, deleted or revoked. Create a new one. |
| `API key is missing permissions: …` | Edit the key in Immich and add the listed permissions, or create a new one. |
| `could not reach Immich` | The app's server can't reach Immich. This is a server setting (`IMMICH_SERVER_URL`), not your key. Tell your admin. |

## Good to know

- **The key is stored encrypted** and is never shown again, not even to admins. To change it,
  paste a new one. **Disconnect** deletes it from the app.
- **Revoking the key in Immich** disconnects you. The next time the app talks to Immich, it asks you
  to connect a new key.
- **Connecting a different Immich account** clears your avatar, because it was one of the previous
  account's people.
- **Daily challenges are per player.** Everyone gets their own daily, built from their own library,
  and the daily leaderboard still ranks everyone's scores for the day together.
- **Other players' avatars** on leaderboards only show if your own Immich account can see that
  person. Otherwise you see a placeholder.

## For admins

- The installation-wide `IMMICH_API_KEY` in `.env` is **optional** now. It's only a fallback for
  admin views, such as reviewing reported photos, used when the admin hasn't connected a key of
  their own. Regular players never use it.
- Set `IMMICH_KEY_ENCRYPTION_SECRET` (`openssl rand -hex 32`) to encrypt stored keys. If it's unset,
  a key derived from `JWT_SECRET` is used instead, which means rotating `JWT_SECRET` also forces
  every player to reconnect. Rotating `IMMICH_KEY_ENCRYPTION_SECRET` forces the same.
- External libraries need **no extra volumes or mounts** on the minigames containers. The app reads
  metadata from Immich's database and gets every image through Immich's API, so anything Immich can
  show, the app can show.

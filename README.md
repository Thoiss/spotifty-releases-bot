# Spotify release bot

Checks every night at **00:03** whether the artists you follow on Spotify
released something new, adds those songs to a playlist of your choice, and
sends you the individual Spotify links over WhatsApp.

---

## How it works

One run is a straight line of six steps:

```
1. Ask Spotify which artists you follow          -> /me/following
2. Ask for each artist's most recent releases    -> /artists/{id}/albums
3. Keep only what is genuinely new               -> release date window + seen-list
4. Expand each release into its tracks           -> /albums/{id}/tracks
5. Add the tracks to your playlist               -> /playlists/{id}/tracks
6. Send one WhatsApp message per song            -> Meta Graph API
```

"Genuinely new" is guarded twice, on purpose:

* a **date window** (`LOOKBACK_DAYS`, default 2 days) stops the bot from
  importing an artist's entire back catalogue;
* a **seen-list** in `data/state.json` stops it from posting the same song
  twice, even if the container restarts or runs twice in one day.

A third guard sits in front of the playlist: tracks already in the playlist are
skipped, so nothing is ever duplicated there either.

---

## Read this before setting up WhatsApp

You asked for messages in a **WhatsApp group**. The official WhatsApp Cloud
API cannot do that — Meta exposes no endpoint for sending into a group chat,
and the same is true for Twilio and every other official reseller. Only
unofficial WhatsApp Web automation (`whatsapp-web.js` and friends) can post to
groups, at the risk of your number being banned.

So this bot sends the songs to **one phone number** (yours) instead. Everything
else works exactly as asked.

There is a second Meta rule worth knowing up front: a **free-form text message
may only be sent within 24 hours** of your last message to the business number.
A bot that fires at 00:03 is almost always outside that window. Two ways out:

| Option | What you do | Trade-off |
| --- | --- | --- |
| **Message template** (recommended) | Create a template in WhatsApp Manager whose body is e.g. `New release: {{1}}`, wait for approval, set `WHATSAPP_TEMPLATE_NAME` | Works unattended, forever. No rich link preview. |
| **Plain text** | Leave `WHATSAPP_TEMPLATE_NAME` empty, and message the business number yourself each day | Rich link previews, but you must keep the window open manually |

If the window closes, the bot logs a clear error (Meta code `131047`) instead
of failing silently.

---

## Setup

### 1. Spotify app

1. Go to <https://developer.spotify.com/dashboard> and create an app.
2. In its settings, add the redirect URI `http://127.0.0.1:8080/callback`.
3. Note the **Client ID** and **Client secret**.

### 2. Get a refresh token

Reading your followed artists and writing to your playlist are things only
*you* may do, so the bot needs a token that represents you. You approve that
once, in a browser, and the resulting refresh token never expires:

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m spotify_release_bot authorize
```

Follow the prompts, approve the permissions, and it prints the three
`SPOTIFY_*` values to paste into your `.env`.

### 3. Playlist ID

Open the playlist in Spotify, `Share -> Copy link to playlist`, and take the id
out of the URL:

```
https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=...
                                  ^^^^^^^^^^^^^^^^^^^^^^
```

### 4. WhatsApp Cloud API

1. Create an app at <https://developers.facebook.com> and add the **WhatsApp**
   product.
2. Under *WhatsApp → API Setup*, note the **Phone number ID** and generate an
   access token. The temporary token expires in 24 hours — generate a permanent
   one via a System User in Business Settings before relying on it.
3. Add your own number as a recipient (required while the app is in test mode).
4. Optional but recommended: create and submit the message template described
   above.

### 5. Configure and run

```bash
cp .env.example .env
nano .env                    # fill in everything

docker compose up -d --build
docker compose logs -f
```

The container stays running and does the check itself at `RUN_AT`; no host cron
or systemd timer is needed. `restart: unless-stopped` brings it back after a
reboot of your Proxmox host.

---

## Testing before you trust it

Run a single check without changing anything:

```bash
docker compose run --rm spotify-release-bot once --dry-run
```

This lists the releases it found, the tracks it would add and the exact
WhatsApp messages it would send, and writes nothing at all. When it looks
right, drop `--dry-run` to do it for real:

```bash
docker compose run --rm spotify-release-bot once
```

Unit tests (no network, no credentials needed):

```bash
pip install -r requirements-dev.txt
python -m pytest
```

---

## Commands

| Command | Purpose |
| --- | --- |
| `run` | Stay running and check daily at `RUN_AT`. This is the container default. |
| `once` | Do one check now and exit. Useful for testing or a host cron job. |
| `authorize` | One-time browser login that prints a Spotify refresh token. |
| `--dry-run` | Add to any command: report, change nothing. |

---

## Settings

All of them live in `.env`; `.env.example` documents each one inline. The ones
you are most likely to touch:

| Variable | Default | Meaning |
| --- | --- | --- |
| `RUN_AT` | `00:03` | Local time of the daily check |
| `TIMEZONE` | `Europe/Amsterdam` | Timezone `RUN_AT` is interpreted in |
| `LOOKBACK_DAYS` | `2` | How many days back a release date may be |
| `SPOTIFY_INCLUDE_GROUPS` | `album,single` | Add `appears_on` to catch features (noisy) |
| `WHATSAPP_MESSAGE_MODE` | `per_track` | `per_track` = one message per song, `summary` = one message total |
| `WHATSAPP_TEMPLATE_NAME` | *(empty)* | Set to send approved templates instead of plain text |
| `WHATSAPP_ENABLED` | `true` | Set `false` to only fill the playlist |
| `DRY_RUN` | `false` | Report without changing anything |

---

## Project layout

```
src/spotify_release_bot/
  __main__.py        CLI: run / once / authorize
  config.py          reads and validates every environment variable
  models.py          Artist, Album, Track - immutable data objects
  spotify_client.py  Spotify Web API: OAuth refresh, paging, rate limits
  release_finder.py  pure logic: "is this release new?" (no network)
  app.py             orchestrates one nightly run
  whatsapp_client.py Meta Graph API: text and template messages
  messages.py        the wording of the WhatsApp messages
  state.py           remembers handled releases across restarts
  scheduler.py       "run every day at HH:MM", DST-aware
  authorize.py       one-time Spotify login helper
tests/               unit tests, all offline
```

The split is deliberate: `release_finder.py` and `messages.py` contain the
decisions worth testing and touch no network, so the test suite can cover the
important behaviour without a single API call.

---

## Troubleshooting

**`Refreshing the Spotify access token failed (400)`**
The refresh token is wrong, revoked, or was issued for different credentials.
Re-run `authorize`.

**`403` when adding tracks**
The refresh token lacks the playlist scopes, or the playlist belongs to someone
else. Re-run `authorize` and make sure you own the playlist.

**`WhatsApp refused the message ... error 131047`**
The 24-hour window is closed. Set `WHATSAPP_TEMPLATE_NAME`, or send a message
from your phone to the business number.

**Nothing is found, but you know something came out**
Spotify publishes per region at local midnight; raise `LOOKBACK_DAYS` to `3`.
Check `SPOTIFY_MARKET` matches your country.

**It posted the same song twice**
`data/state.json` was deleted or the volume is not mounted. Check that
`./data` exists on the host and is writable by uid `10001`.

---

## Security notes

* `.env` holds credentials that give full access to your Spotify account and
  your WhatsApp business number. It is in `.gitignore`; keep it that way.
* The container runs as a non-root user (uid `10001`).
* The bot only ever needs outbound HTTPS to `api.spotify.com`,
  `accounts.spotify.com` and `graph.facebook.com`.

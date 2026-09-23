# Setup guide

Follow this once, top to bottom. It takes about 30 minutes, most of which is
waiting for Meta to approve a message template.

The work splits over two machines:

| Where | What happens there | Why |
| --- | --- | --- |
| **Your laptop** | Spotify login, WhatsApp setup | Both need a browser |
| **Your server** | The container that runs every night | It is always on |

The only thing that travels between them is the contents of `.env`.

---

## Step 0 — Check the server has what it needs

On the server:

```bash
docker --version
docker compose version
```

Both must print a version. If `docker compose` fails but `docker-compose`
works, you are on the old standalone tool: use `docker-compose` everywhere
below instead.

---

## Step 1 — Create a Spotify app (laptop)

The bot talks to Spotify as *an app acting on your behalf*, so first you
register the app.

1. Go to <https://developer.spotify.com/dashboard> and log in.
2. **Create app**.
   - *App name*: anything, e.g. `release-bot`
   - *Redirect URI*: `http://127.0.0.1:8080/callback`
   - *Which API/SDKs*: tick **Web API**
3. Save, then open the app's **Settings**.
4. Copy the **Client ID**, and click *View client secret* to copy the
   **Client secret**.

> The redirect URI must match **character for character**, including the
> `http://` and the `/callback`. A mismatch is the single most common cause of
> "INVALID_CLIENT: Invalid redirect URI" in the next step.

---

## Step 2 — Get a refresh token (laptop)

Reading your followed artists and writing to your playlist are things only
*you* can authorise, so the bot needs a token that represents you personally.
You approve that once in a browser; the resulting refresh token does not
expire, so the server never needs a browser again.

Run this **on your laptop**, because the browser has to be able to reach
`127.0.0.1:8080`.

> **Python 3.11 or newer is required.** Check with `python3 --version`
> (macOS/Linux) or `py --version` (Windows). On Windows, `python3` is not a
> real command — it hits a Microsoft Store stub that silently does nothing.
> Use `py`, the Python launcher, instead.

**macOS / Linux**

```bash
git clone https://github.com/Thoiss/spotifty-releases-bot
cd spotifty-releases-bot
git checkout claude/cool-ride-trzeoa

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

PYTHONPATH=src python -m spotify_release_bot authorize
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/Thoiss/spotifty-releases-bot
cd spotifty-releases-bot
git checkout claude/cool-ride-trzeoa

py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:PYTHONPATH = "src"
python -m spotify_release_bot authorize
```

Note that `PYTHONPATH=src python ...` is shell syntax that does **not** work in
PowerShell — set the variable on its own line as shown above.

<details>
<summary>Windows: two things that commonly get in the way</summary>

**No suitable Python.** Install a current one and point the launcher at it:

```powershell
winget install Python.Python.3.12
py -3.12 -m venv .venv
```

**"Running scripts is disabled on this system"** when activating. Allow it for
this one terminal session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```
</details>

It asks for the client ID, the client secret and the redirect URI, then prints
a long Spotify URL. Open it, approve the permissions, and the helper catches
the redirect automatically and prints three lines:

```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REFRESH_TOKEN=...
```

Keep them; they go into `.env` in step 5.

<details>
<summary>Prefer to run it directly on the headless server?</summary>

Forward the port over SSH first, so your laptop's browser can reach the
listener running on the server:

```bash
ssh -L 8080:localhost:8080 user@yourserver
```

Then run the `authorize` command inside that SSH session. The redirect to
`127.0.0.1:8080` on your laptop is tunnelled to the server.
</details>

### Re-authorising later

A refresh token keeps the permissions it was created with, forever. If an
update adds a scope — or a `check` run says the token lacks one — you have to
run `authorize` again; updating the code alone changes nothing.

It is the same two-machine dance as the first time:

1. **On your laptop**, `git pull` first (otherwise the old scopes are
   requested), activate the venv, and run `authorize`.
2. **On your server**, `git pull`, replace `SPOTIFY_REFRESH_TOKEN` in `.env`
   with the new value, then `docker compose build`.

The client ID and secret never change, and neither does the playlist id. Only
the refresh token is replaced.

---

## Step 3 — Get the playlist ID (laptop)

1. In Spotify, create the playlist the new songs should land in (or pick an
   existing one). **You must own it** — the bot cannot write to someone
   else's playlist.
2. Right-click it → *Share* → *Copy link to playlist*.
3. The ID is the part between `/playlist/` and `?`:

```
https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123
                                  └────────┬───────────┘
                                    this is the ID
```

---

## Step 4 — Set up WhatsApp Cloud API (laptop)

### 4a. Create the app

1. Go to <https://developers.facebook.com> → **My Apps** → **Create App**.
2. Choose use case **Other**, then app type **Business**.
3. On the app dashboard, find **WhatsApp** and click **Set up**.

### 4b. Collect the credentials

Open **WhatsApp → API Setup**. Note:

- **Phone number ID** — the long number under the test number (*not* the phone
  number itself)
- **Temporary access token** — valid 24 hours, fine for testing

Under *To*, add **your own phone number** as a recipient and confirm the code
WhatsApp sends you. While the app is in test mode you can only message numbers
added here.

### 4c. Make the token permanent

The temporary token dies after 24 hours, which would stop the bot overnight.
Create a lasting one.

> **Business Settings is not in the app dashboard.** It lives on a different
> site: <https://business.facebook.com/settings> (in Dutch,
> *Bedrijfsinstellingen*). Going straight to
> <https://business.facebook.com/settings/system-users> saves the hunt.

1. In the left sidebar: **Users** → **System users** → **Add**.
   - Name `release-bot`, role **Admin**.
2. **Assign assets** — you need **both** of these, not just the app:
   - **Apps** → your app → **Full control** / *Manage app*
   - **WhatsApp accounts** → your WhatsApp Business account → **Full control** /
     *Manage WhatsApp business accounts*
3. **Generate new token** → select your app → tick:
   - `whatsapp_business_messaging`
   - `whatsapp_business_management`
4. Set expiry to **Never** and copy the token. It is shown once only.

Assigning only the app is the usual mistake: the token is created happily, then
every send fails because it has no rights over the phone number. If that
happens, go back to step 2 and add the WhatsApp account asset.

<details>
<summary>The business portfolio selector is empty or there is no business</summary>

The app is not linked to a business portfolio yet. In the app dashboard go to
**App settings → Basic**, find the **Business Account** field
(*Bedrijfsaccount*), and link or create one. Then return to Business Settings.
</details>

### 4d. Create a message template (strongly recommended)

WhatsApp only allows free-form text within 24 hours of *you* messaging the
business number. A bot firing at 00:03 is almost always outside that window,
so without a template you would have to message it manually every single day.

1. **WhatsApp Manager** → **Message templates** → **Create template**
2. Category: **Utility**
3. Name: `new_release` (lowercase and underscores only)
4. Language: English (or Dutch — whatever you set in `WHATSAPP_TEMPLATE_LANGUAGE`)
5. **Type variabele / Variable format: Nummer** (*Number* — positional).
   This is the dropdown above the content fields, and it defaults to *Naam*
   (*Name*). It must be **Nummer**, or the bot cannot fill the template in —
   see the warning below.
6. **Header: None** (*Geen*). Leave *Koptekst*, footer and buttons empty too.
7. Body (*Tekst*) — exactly one variable:

   ```
   New release: {{1}}
   ```

8. Provide a sample value when asked, e.g.
   `Artist - Song https://open.spotify.com/track/abc`
9. Submit. Approval usually takes minutes, sometimes a day.

<details>
<summary>The editor keeps rejecting the header, or you would rather skip it</summary>

The template editor sometimes submits an empty `HEADER` component even when
the header fields look blank, and then refuses to save with *"(text) is
missing in the component of type HEADER"*. Two ways out:

- **Give the header static text.** Type something fixed into *Koptekst*, e.g.
  `New release`, and do not add a variable to it. An empty header is invalid;
  a filled one is fine, and the bot never has to supply anything for it.
- **Create the template over the API**, which builds exactly one `BODY`
  component so no header can appear:

  ```bash
  curl -X POST "https://graph.facebook.com/v21.0/<WABA_ID>/message_templates" \
    -H "Authorization: Bearer <ACCESS_TOKEN>" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "new_release",
      "language": "en",
      "category": "MARKETING",
      "parameter_format": "POSITIONAL",
      "components": [{
        "type": "BODY",
        "text": "New release: {{1}}",
        "example": { "body_text": [["Artist - Song https://open.spotify.com/track/abc"]] }
      }]
    }'
  ```

  `parameter_format: POSITIONAL` is what produces `{{1}}` rather than a named
  variable. If the call reports that the name already exists, delete the old
  draft in WhatsApp Manager first, or pick another name and set
  `WHATSAPP_TEMPLATE_NAME` to match.
</details>

> **The variable must read `{{1}}`, not `{{something}}`.** WhatsApp templates
> come in two flavours: *positional* (`{{1}}`, `{{2}}`) and *named*
> (`{{movie_name}}`). They are filled in differently over the API — a named
> template needs a `parameter_name` on every parameter. The bot sends
> positional parameters, so a named template is rejected at send time, every
> night, long after approval succeeded. If the preview on the right shows
> anything other than `{{1}}`, change *Type variabele* to **Nummer** and
> re-insert the variable.

> **Set the header to None, not empty.** If the header type is left on *Text*
> with nothing typed in it, saving fails with *"an expected field (or fields)
> (text) is missing in the component of type HEADER"*. The bot sends a body
> parameter only, so the template must not ask for anything else.
>
> A header containing **static** text is harmless if you want one. A header
> with its own `{{1}}` variable is not — the bot would have to send a header
> component as well, and every message would be rejected.

---

## Step 5 — Deploy on the server

```bash
ssh user@yourserver

git clone https://github.com/Thoiss/spotifty-releases-bot
cd spotifty-releases-bot
git checkout claude/cool-ride-trzeoa

cp .env.example .env
nano .env          # paste everything you collected above
```

### Create the data directory with the right owner

This is the step people miss. The container runs as **uid 10001**, not root.
If Docker creates `./data` itself it will be owned by root and the container
cannot write its state file — which means it would repost the same songs every
night.

```bash
mkdir -p data
sudo chown 10001:10001 data
```

### Build and test

```bash
docker compose build

# Shows exactly what it would do, changes absolutely nothing:
docker compose run --rm spotify-release-bot once --dry-run
```

Read that output carefully. It lists the releases it found, the tracks it
would add, and the exact WhatsApp messages it would send.

Start small, because every artist costs one API request and a full run of
several hundred is exactly what exhausts the Spotify rate limit:

```bash
docker compose run --rm spotify-release-bot once --dry-run --max-artists 10
```

`0 new release(s)` there is a healthy result, not a failure — it only means
none of those ten artists released anything in the last two days. To prove the
rest of the pipeline works, widen the window for one run so it has something to
chew on:

```bash
docker compose run --rm spotify-release-bot once --dry-run --max-artists 10 --lookback-days 30
```

Now you should see releases listed, tracks that would be added, and the exact
messages. Keep `--dry-run` on for that one: without it, a 30-day window would
genuinely dump a month of back catalogue into your playlist.

When it looks right, do it for real once:

```bash
docker compose run --rm spotify-release-bot once
```

Check that the songs appeared in your playlist and that WhatsApp arrived.

### Start it for good

```bash
docker compose up -d
docker compose logs -f
```

You should see:

```
Starting scheduler: daily at 00:03 (Europe/Amsterdam)
Next check at 2026-09-15 00:03 CEST (in 11h 42m)
```

Press `Ctrl+C` to stop following the logs — that does not stop the container.
`restart: unless-stopped` brings it back after a reboot of the host.

---

## Step 6 — Confirm it actually ran

The morning after:

```bash
cd ~/spotifty-releases-bot
docker compose logs --since 24h | grep "Run finished"
```

Expect something like:

```
Run finished: checked 143 artists, 2 new release(s), 3 track(s) added,
0 duplicate(s) skipped, 3 WhatsApp message(s) sent
```

---

## Everyday commands

| Goal | Command |
| --- | --- |
| Follow the logs | `docker compose logs -f` |
| Run a check right now | `docker compose run --rm spotify-release-bot once` |
| See what it *would* do | `docker compose run --rm spotify-release-bot once --dry-run` |
| Cheap test on a few artists | `docker compose run --rm spotify-release-bot once --dry-run --max-artists 10` |
| Change a setting | `nano .env` then `docker compose up -d --force-recreate` |
| Update to newer code | `git pull && docker compose up -d --build` |
| Stop it | `docker compose down` |
| Start again | `docker compose up -d` |
| Forget its history | `docker compose down && rm data/state.json && docker compose up -d` |

---

## When something goes wrong

**`Configuration problem: Required environment variable ... is missing`**
A line in `.env` is empty or misspelled. Compare against `.env.example`.

**`Refreshing the Spotify access token failed (400)`**
The refresh token is wrong or was issued for different credentials. Redo
step 2, and make sure all three `SPOTIFY_*` values come from the *same* run.

**"Geen toestemmingen beschikbaar" / "No permissions available" when generating a token**
The system user has no role on the app, so there is nothing to grant.
Selecting the app inside the token wizard is not the same as being assigned to
it. Go to the system user, **Assign assets → Apps**, tick the app, enable
**Full control**, and save. Then generate the token again. The same panel is
reachable from the other direction: **Accounts → Apps → [your app] → Add
people → [the system user]**.

**Lost the permanent token**
It cannot be retrieved — Meta shows it once. Generate a new one and paste it
into `.env` before closing the dialog.

**`403` on `/playlists/{id}/tracks` specifically**
Spotify removed that sub-resource in its February 2026 migration, and apps in
Development mode get a bare 403 from it rather than a deprecation notice. It is
now `/playlists/{id}/items`. Update the bot:
`git pull && docker compose up -d --build`. Reading the playlist's *metadata*
keeps working throughout, which is what makes this look like a permission
problem when it is not.

**`405` when adding tracks**
The playlist id carried its `?si=...` tracking tail, which swallowed the rest
of the request path. Recent versions strip it automatically — update with
`git pull && docker compose up -d --build` — or set `SPOTIFY_PLAYLIST_ID` to
the bare id.

**`403` when adding tracks**
You do not own the playlist, or the token lacks the playlist scopes. Check the
playlist ID and redo step 2.

**Messages rejected although the template is approved**
The template probably uses *named* variables (`{{song}}`) instead of
positional ones (`{{1}}`). Open it in WhatsApp Manager, set *Type variabele*
to **Nummer**, and make the body read `New release: {{1}}`.

**`an expected field (or fields) (text) is missing in the component of type HEADER`**
The template has a header whose text is empty. Set the header to **None**
(*Geen*) in the template editor, or type static text into it.

**`error 131047`**
The 24-hour WhatsApp window is closed. Set `WHATSAPP_TEMPLATE_NAME=new_release`
in `.env` (step 4d), or send any message from your phone to the business
number to reopen it.

**`Could not write the state file`**
The `data` directory is owned by root. Fix it:
```bash
sudo chown 10001:10001 data
```

**`Invalid limit` on every artist**
You are on an old version of the code, which asked Spotify for 50 albums per
artist; that endpoint now rejects anything above 10. Update with
`git pull && docker compose up -d --build`.

**`Spotify has rate limited this app for Xh Ym`**
Too many requests too quickly. Spotify blocks an app for hours once that
happens, and nothing can shorten it — the block has to expire.

Every followed artist costs one request, so a few hundred artists is a few
hundred requests per run. Apps in **Development mode** have a much smaller
quota than approved ones, which is why this bites even at a modest pace.

Once the block expires:

1. Make sure `SPOTIFY_REQUEST_DELAY=1.0` (or higher) in `.env`. At one second
   per artist a 350-artist run takes about six minutes, which is irrelevant for
   a job that runs at night.
2. Test with a handful of artists first, so a mistake costs almost nothing:

   ```bash
   docker compose run --rm spotify-release-bot once --dry-run --max-artists 10
   ```

3. Only once that works, run the full check.

**It found nothing, but you know something came out**
Spotify publishes per region at local midnight. Raise `LOOKBACK_DAYS` to `3`
in `.env` and check `SPOTIFY_MARKET=NL` matches your country.

**It posted the same song twice**
The state file was lost. Check that `./data` exists, is owned by uid 10001,
and that `docker compose config` shows the volume mounted.

---

## What to keep safe

`.env` gives full access to your Spotify account and your WhatsApp business
number. It is already in `.gitignore`. Never commit it, and never paste its
contents into a chat, an issue, or a screenshot.

"""The ``check`` command: prove the credentials and playlist are usable.

Permission failures from Spotify all surface as a bare 403, which says nothing
about *which* of several unrelated causes applies: the token might belong to a
different account than the playlist, the playlist might not be writable by you,
or the token might simply predate a change in requested scopes. This walks the
same path a real run takes and reports what it finds at each step, so the
answer is a sentence rather than a guess.
"""

from __future__ import annotations

import logging

from .config import AppConfig
from .spotify_client import REQUIRED_SCOPES, SpotifyClient, SpotifyError

_LOG = logging.getLogger(__name__)


def check(config: AppConfig, spotify: SpotifyClient) -> bool:
    """Run every check. Returns True when the bot should be able to work."""
    ok = True

    # 1. Does the refresh token still produce an access token?
    try:
        me = spotify.current_user()
    except SpotifyError as exc:
        _LOG.error("Could not authenticate with Spotify: %s", exc)
        _LOG.error("Fix: re-run 'python -m spotify_release_bot authorize'.")
        return False

    my_id = me.get("id", "")
    _LOG.info("Authenticated as %s (%s)", me.get("display_name") or my_id, my_id)

    # 1b. Which permissions does this token actually carry? Scopes are fixed
    # when the refresh token is issued, so this distinguishes "the code was
    # updated" from "the token was replaced" - which look identical otherwise.
    granted = spotify.granted_scopes
    missing = [scope for scope in REQUIRED_SCOPES if scope not in granted]
    _LOG.info("Token scopes: %s", ", ".join(granted) or "(none reported)")
    if missing:
        ok = False
        _LOG.error("Token is MISSING these scopes: %s", ", ".join(missing))
        _LOG.error(
            "Fix: run authorize again on the machine with a browser, and make sure "
            "the new SPOTIFY_REFRESH_TOKEN really replaced the old one in .env on "
            "the server. A token never gains scopes it was not created with."
        )
    else:
        _LOG.info("All required scopes present.")

    # 2. Can we see the followed artists?
    try:
        artists = spotify.followed_artists()
        _LOG.info("Followed artists readable: %d", len(artists))
    except SpotifyError as exc:
        ok = False
        _LOG.error("Cannot read followed artists: %s", exc)
        _LOG.error("Fix: the token lacks 'user-follow-read'; re-run authorize.")

    # 3. Does the playlist exist, and who owns it?
    playlist_id = config.spotify.playlist_id
    try:
        playlist = spotify.playlist(playlist_id)
    except SpotifyError as exc:
        _LOG.error("Cannot read playlist %s: %s", playlist_id, exc)
        _LOG.error(
            "Fix: check SPOTIFY_PLAYLIST_ID is the id of a playlist on THIS account, "
            "then re-run authorize so the token carries the playlist scopes: %s",
            ", ".join(REQUIRED_SCOPES),
        )
        return False

    owner_id = (playlist.get("owner") or {}).get("id", "")
    owner_name = (playlist.get("owner") or {}).get("display_name") or owner_id
    _LOG.info(
        "Playlist %r  owner=%s  public=%s  collaborative=%s",
        playlist.get("name", "?"),
        owner_name,
        playlist.get("public"),
        playlist.get("collaborative"),
    )

    if owner_id and my_id and owner_id != my_id:
        ok = False
        _LOG.error(
            "This playlist belongs to %s, not to you. A public playlist can be "
            "read by anyone but only its owner (or a collaborator) may add "
            "tracks, so the bot cannot write here.",
            owner_name,
        )
        _LOG.error(
            "Fix: create your own playlist and use its id, or have the owner "
            "make you a collaborator."
        )

    # 4. Can we actually read its tracks? This is what a real run does first.
    try:
        existing = spotify.playlist_track_ids(playlist_id)
        _LOG.info("Playlist tracks readable: %d already in it", len(existing))
    except SpotifyError as exc:
        ok = False
        _LOG.error("Cannot read the playlist's tracks: %s", exc)
        if missing:
            _LOG.error("Almost certainly the missing scopes listed above.")
        else:
            # The scopes are right, so narrow it down: retry without the query
            # parameters the normal call adds, to see whether one of those is
            # what Spotify objects to rather than the permission itself.
            try:
                spotify.raw_get(f"/playlists/{playlist_id}/tracks", {"limit": 1})
                _LOG.error(
                    "A plain request for the same tracks DID work, so the "
                    "permission is fine and one of the query parameters is at "
                    "fault. Please report this output."
                )
            except SpotifyError as plain_exc:
                _LOG.error("A plain request fails too: %s", plain_exc)
                _LOG.error(
                    "Scopes are correct and you own the playlist, so this is "
                    "unusual. Check whether the Spotify app is still in "
                    "Development mode and that this account is listed under "
                    "the app's User Management."
                )

    if ok:
        _LOG.info("All checks passed. The bot should be able to run.")
    else:
        _LOG.error("Some checks failed; see the fixes above.")
    return ok

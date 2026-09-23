"""A thin Spotify Web API client, built on ``requests``.

Only the handful of endpoints this bot needs are implemented. Writing it by
hand (instead of pulling in a large wrapper library) keeps the OAuth refresh
flow visible, which is the part that actually matters for an unattended bot.

Authentication model
--------------------
Reading "artists I follow" and writing to a playlist are *user* actions, so the
bot needs a user token, not a plain app token. We get one with the Authorization
Code flow **once** (see ``authorize.py``), store the long-lived refresh token,
and exchange it for a short-lived access token on every run.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Iterator

import requests

from .models import Album, Artist, Track

_LOG = logging.getLogger(__name__)

ACCOUNTS_TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE_URL = "https://api.spotify.com/v1"

# Scopes the refresh token must have been granted.
REQUIRED_SCOPES = (
    "user-follow-read",              # read the artists you follow
    "playlist-read-private",         # read a private playlist's existing tracks
    "playlist-read-collaborative",   # ... or a collaborative one
    "playlist-modify-private",       # add tracks to a private playlist
    "playlist-modify-public",        # ... or a public one
)

_MAX_TRACKS_PER_ADD = 100  # hard limit set by the Spotify API

# /artists/{id}/albums rejects anything above 10 with "Invalid limit", even
# though the documentation still says 50. Other paged endpoints do accept 50.
_MAX_ALBUM_PAGE_LIMIT = 10

# A Retry-After longer than this means we are properly blocked, not briefly
# throttled; sitting in a retry loop would only make it worse.
_UNRECOVERABLE_RETRY_AFTER_SECONDS = 300

_MAX_ATTEMPTS = 4


class SpotifyError(RuntimeError):
    """Any failure while talking to Spotify that we cannot recover from."""


class SpotifyRateLimited(SpotifyError):
    """Spotify has blocked us for a long period.

    Kept separate from ``SpotifyError`` because the caller must treat it
    differently: a single artist failing is worth skipping over, but being
    rate limited means every following request would fail too, so the run has
    to stop instead of hammering the API a few hundred more times.
    """


class SpotifyClient:
    """Stateful client: it owns one access token and refreshes it when needed."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        market: str = "NL",
        session: requests.Session | None = None,
        timeout: float = 20.0,
        request_delay: float = 0.35,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._market = market
        self._session = session or requests.Session()
        self._timeout = timeout
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0
        self._request_delay = max(request_delay, 0.0)
        self._last_request_at = 0.0
        self._granted_scopes: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def _ensure_access_token(self) -> str:
        """Return a valid access token, refreshing it shortly before it expires."""
        if self._access_token and time.monotonic() < self._access_token_expires_at:
            return self._access_token

        basic = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode("utf-8")
        ).decode("ascii")
        try:
            response = self._session.post(
                ACCOUNTS_TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": self._refresh_token},
                headers={"Authorization": f"Basic {basic}"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise SpotifyError(f"Could not reach Spotify token endpoint: {exc}") from exc

        if response.status_code != 200:
            raise SpotifyError(
                "Refreshing the Spotify access token failed "
                f"({response.status_code}): {response.text[:300]}. "
                "Re-run 'python -m spotify_release_bot authorize' to get a new refresh token."
            )

        payload = response.json()
        self._access_token = payload["access_token"]
        # Spotify echoes the scopes the refresh token actually carries. They are
        # fixed at authorisation time, so this is the only reliable way to tell
        # a stale token from a fresh one.
        self._granted_scopes = tuple(sorted((payload.get("scope") or "").split()))
        # Refresh 60s early so a long run never trips over an expiring token.
        self._access_token_expires_at = time.monotonic() + int(payload.get("expires_in", 3600)) - 60

        # Spotify may hand out a rotated refresh token; use it for the rest of
        # this process. It is not persisted, the old one keeps working.
        rotated = payload.get("refresh_token")
        if rotated and rotated != self._refresh_token:
            _LOG.info("Spotify issued a rotated refresh token for this session")
            self._refresh_token = rotated

        return self._access_token

    @property
    def granted_scopes(self) -> tuple[str, ...]:
        """Scopes the refresh token carries; empty until the first refresh."""
        return self._granted_scopes

    def raw_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Escape hatch for the diagnostics: one GET, exactly as specified."""
        return self._request("GET", path, params=params or {})

    # ------------------------------------------------------------------
    # Low level request helper
    # ------------------------------------------------------------------
    def _wait_for_slot(self) -> None:
        """Keep a minimum gap between requests.

        Checking a few hundred followed artists means a few hundred calls. Sent
        back to back they trip Spotify's rate limiter, and the penalty is not a
        short pause but a block measured in hours. Spacing the calls out costs
        a couple of minutes once a night and avoids that entirely.
        """
        if self._request_delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._request_delay:
            time.sleep(self._request_delay - elapsed)
        self._last_request_at = time.monotonic()

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Perform one API call, retrying on rate limits and transient errors.

        Spotify answers 429 with a ``Retry-After`` header telling us how many
        seconds to wait; honouring it is the difference between a bot that keeps
        working and one that gets throttled harder.
        """
        if not url.startswith("http"):
            url = f"{API_BASE_URL}{url}"

        last_error = ""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            self._wait_for_slot()
            token = self._ensure_access_token()
            headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
            try:
                response = self._session.request(
                    method, url, headers=headers, timeout=self._timeout, **kwargs
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                _LOG.warning("Spotify request failed (attempt %d): %s", attempt, exc)
                time.sleep(min(2 ** attempt, 16))
                continue

            if response.status_code == 429:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                if retry_after > _UNRECOVERABLE_RETRY_AFTER_SECONDS:
                    # Spotify blocks an app for hours once it has been hammered.
                    # Retrying cannot help, and every extra call risks extending
                    # the block, so stop the run here with a clear explanation.
                    raise SpotifyRateLimited(
                        f"Spotify has rate limited this app for {retry_after // 3600}h "
                        f"{(retry_after % 3600) // 60}m (Retry-After: {retry_after}s). "
                        "No further requests will succeed until that expires. "
                        "Raise SPOTIFY_REQUEST_DELAY so the next run paces itself."
                    )
                _LOG.warning("Rate limited by Spotify; waiting %ds", retry_after)
                time.sleep(retry_after)
                continue

            if response.status_code == 401:
                # Token went stale mid-run: drop it and let the next loop refresh.
                _LOG.info("Spotify access token rejected; forcing a refresh")
                self._access_token = None
                self._access_token_expires_at = 0.0
                last_error = "401 Unauthorized"
                continue

            if response.status_code >= 500:
                last_error = f"{response.status_code} {response.text[:200]}"
                _LOG.warning("Spotify server error (attempt %d): %s", attempt, last_error)
                time.sleep(min(2 ** attempt, 16))
                continue

            if response.status_code >= 400:
                raise SpotifyError(
                    f"{method} {url} failed ({response.status_code}): {response.text[:300]}"
                )

            if not response.content:
                return {}
            return response.json()

        raise SpotifyError(f"{method} {url} kept failing after {_MAX_ATTEMPTS} attempts: {last_error}")

    def _paginate(self, url: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Walk a standard offset-paginated Spotify collection, yielding items."""
        page = self._request("GET", url, params=params)
        while True:
            for item in page.get("items", []):
                if item is not None:
                    yield item
            next_url = page.get("next")
            if not next_url:
                return
            page = self._request("GET", next_url)

    # ------------------------------------------------------------------
    # Endpoints this bot uses
    # ------------------------------------------------------------------
    def followed_artists(self) -> list[Artist]:
        """Every artist the authenticated user follows.

        This endpoint uses *cursor* paging (an ``after`` marker) rather than the
        usual offset paging, so it needs its own loop.
        """
        artists: list[Artist] = []
        params: dict[str, Any] = {"type": "artist", "limit": 50}
        while True:
            payload = self._request("GET", "/me/following", params=params).get("artists", {})
            for item in payload.get("items", []):
                artists.append(Artist(id=item["id"], name=item["name"]))
            cursor = (payload.get("cursors") or {}).get("after")
            if not cursor or not payload.get("next"):
                break
            params = {"type": "artist", "limit": 50, "after": cursor}
        _LOG.info("You follow %d artists", len(artists))
        return artists

    def artist_albums(
        self,
        artist_id: str,
        include_groups: tuple[str, ...],
        max_pages: int = 1,
    ) -> list[Album]:
        """Recent releases for one artist.

        Spotify returns the newest releases first, so for a nightly check a
        single page is plenty; ``max_pages`` exists for the first run or for
        very prolific artists.

        The page size is capped at 10 because this endpoint rejects anything
        larger with a 400 "Invalid limit", despite the documentation still
        advertising 50.
        """
        albums: list[Album] = []
        params = {
            "include_groups": ",".join(include_groups),
            "limit": _MAX_ALBUM_PAGE_LIMIT,
            "market": self._market,
        }
        url = f"/artists/{artist_id}/albums"
        for _ in range(max_pages):
            page = self._request("GET", url, params=params)
            for item in page.get("items", []):
                albums.append(_album_from_payload(item))
            next_url = page.get("next")
            if not next_url:
                break
            url, params = next_url, {}
        return albums

    def album_tracks(self, album_id: str, album_name: str) -> list[Track]:
        """All playable tracks on one album/single."""
        tracks: list[Track] = []
        for item in self._paginate(f"/albums/{album_id}/tracks", {"limit": 50, "market": self._market}):
            track = _track_from_payload(item, album_name)
            if track is not None:
                tracks.append(track)
        return tracks

    def current_user(self) -> dict[str, Any]:
        """The account the refresh token belongs to."""
        return self._request("GET", "/me")

    def playlist(self, playlist_id: str) -> dict[str, Any]:
        """Metadata for one playlist: name, owner, visibility."""
        return self._request(
            "GET",
            f"/playlists/{playlist_id}",
            params={"fields": "id,name,public,collaborative,owner(id,display_name)"},
        )

    def playlist_track_ids(self, playlist_id: str) -> set[str]:
        """IDs already in the target playlist, so we never add a duplicate.

        Uses ``/items`` rather than ``/tracks``: Spotify's February 2026
        migration removed the ``/tracks`` sub-resource, and apps in Development
        mode get a bare 403 from it rather than a deprecation notice. The entry
        holding the track was renamed from ``track`` to ``item`` at the same
        time, so both spellings are accepted here.
        """
        ids: set[str] = set()
        params = {"fields": "items(item(id),track(id)),next", "limit": 100}
        for entry in self._paginate(f"/playlists/{playlist_id}/items", params):
            track = entry.get("item") or entry.get("track") or {}
            track_id = track.get("id") if isinstance(track, dict) else None
            if track_id:
                ids.add(track_id)
        _LOG.info("Target playlist currently holds %d tracks", len(ids))
        return ids

    def add_tracks_to_playlist(self, playlist_id: str, uris: list[str]) -> int:
        """Append tracks, in chunks of 100 because that is the API maximum.

        Posts to ``/items`` for the same reason as the read above: ``/tracks``
        was removed in February 2026.
        """
        added = 0
        for start in range(0, len(uris), _MAX_TRACKS_PER_ADD):
            chunk = uris[start : start + _MAX_TRACKS_PER_ADD]
            self._request("POST", f"/playlists/{playlist_id}/items", json={"uris": chunk})
            added += len(chunk)
            _LOG.info("Added %d track(s) to playlist %s", len(chunk), playlist_id)
        return added


def _album_from_payload(item: dict[str, Any]) -> Album:
    """Translate one raw API album object into our own ``Album``."""
    artists = tuple(a.get("name", "") for a in item.get("artists", []) if a.get("name"))
    external = item.get("external_urls") or {}
    return Album(
        id=item["id"],
        name=item.get("name", "Unknown album"),
        artists=artists or ("Unknown artist",),
        release_date=item.get("release_date", ""),
        release_date_precision=item.get("release_date_precision", "day"),
        album_group=item.get("album_group", item.get("album_type", "album")),
        url=external.get("spotify", f"https://open.spotify.com/album/{item['id']}"),
    )


def _track_from_payload(item: dict[str, Any], album_name: str) -> Track | None:
    """Translate one raw API track object; returns None for unplayable entries.

    Local files and region-restricted tracks come back without a usable URI and
    would make the playlist call fail, so they are dropped here.
    """
    track_id = item.get("id")
    uri = item.get("uri")
    if not track_id or not uri or item.get("is_local"):
        return None
    artists = tuple(a.get("name", "") for a in item.get("artists", []) if a.get("name"))
    external = item.get("external_urls") or {}
    return Track(
        id=track_id,
        uri=uri,
        name=item.get("name", "Unknown track"),
        artists=artists or ("Unknown artist",),
        url=external.get("spotify", f"https://open.spotify.com/track/{track_id}"),
        album_name=album_name,
    )


def _parse_retry_after(header_value: str | None) -> int:
    """Seconds to wait from a Retry-After header, defaulting to a short pause."""
    try:
        return max(int(header_value or "2"), 1) + 1
    except ValueError:
        return 3

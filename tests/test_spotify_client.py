"""Tests for the Spotify client, using a fake HTTP session.

These cover the fiddly parts that are easy to get wrong and painful to debug
at 00:03: token refreshing, paging and rate limiting.
"""

from __future__ import annotations

import json

import pytest

from spotify_release_bot.spotify_client import (
    SpotifyClient,
    SpotifyError,
    SpotifyRateLimited,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {"Content-Type": "application/json"}
        self.text = text or json.dumps(self._payload)

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")

    def json(self):
        return self._payload


class FakeSession:
    """Returns queued responses and records what was asked for."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[tuple[str, str]] = []

    def post(self, url, **kwargs):
        return self._next(("POST", url))

    def request(self, method, url, **kwargs):
        return self._next((method, url))

    def _next(self, record):
        self.requests.append(record)
        if not self._responses:
            raise AssertionError(f"No fake response queued for {record}")
        return self._responses.pop(0)


def token_response(expires_in=3600):
    return FakeResponse(payload={"access_token": "access-abc", "expires_in": expires_in})


def make_client(session):
    return SpotifyClient(
        "id", "secret", "refresh", market="NL", session=session, timeout=1, request_delay=0.0
    )


def test_followed_artists_walks_cursor_pages():
    session = FakeSession(
        [
            token_response(),
            FakeResponse(
                payload={
                    "artists": {
                        "items": [{"id": "a1", "name": "One"}],
                        "next": "https://api.spotify.com/v1/me/following?after=a1",
                        "cursors": {"after": "a1"},
                    }
                }
            ),
            FakeResponse(
                payload={
                    "artists": {
                        "items": [{"id": "a2", "name": "Two"}],
                        "next": None,
                        "cursors": {"after": None},
                    }
                }
            ),
        ]
    )
    artists = make_client(session).followed_artists()
    assert [a.id for a in artists] == ["a1", "a2"]


def test_access_token_is_reused_across_calls():
    session = FakeSession(
        [
            token_response(),
            FakeResponse(payload={"artists": {"items": [], "next": None, "cursors": {}}}),
            FakeResponse(payload={"items": [], "next": None}),
        ]
    )
    client = make_client(session)
    client.followed_artists()
    client.artist_albums("a1", include_groups=("single",))
    # Exactly one call to the token endpoint, not one per request.
    token_calls = [r for r in session.requests if "accounts.spotify.com" in r[1]]
    assert len(token_calls) == 1


def test_rate_limit_is_retried_after_the_requested_delay(monkeypatch):
    monkeypatch.setattr("spotify_release_bot.spotify_client.time.sleep", lambda _: None)
    session = FakeSession(
        [
            token_response(),
            FakeResponse(status_code=429, headers={"Retry-After": "1"}, payload={}),
            FakeResponse(payload={"items": [], "next": None}),
        ]
    )
    assert make_client(session).artist_albums("a1", include_groups=("single",)) == []


def test_expired_token_triggers_a_refresh_and_a_retry(monkeypatch):
    monkeypatch.setattr("spotify_release_bot.spotify_client.time.sleep", lambda _: None)
    session = FakeSession(
        [
            token_response(),
            FakeResponse(status_code=401, payload={}),
            token_response(),
            FakeResponse(payload={"items": [], "next": None}),
        ]
    )
    assert make_client(session).artist_albums("a1", include_groups=("single",)) == []


def test_a_client_error_is_raised_immediately():
    session = FakeSession(
        [token_response(), FakeResponse(status_code=403, payload={}, text="Forbidden")]
    )
    with pytest.raises(SpotifyError, match="403"):
        make_client(session).artist_albums("a1", include_groups=("single",))


def test_bad_refresh_token_gives_an_actionable_error():
    session = FakeSession([FakeResponse(status_code=400, payload={}, text="invalid_grant")])
    with pytest.raises(SpotifyError, match="authorize"):
        make_client(session).followed_artists()


def test_album_payload_is_translated():
    session = FakeSession(
        [
            token_response(),
            FakeResponse(
                payload={
                    "items": [
                        {
                            "id": "alb1",
                            "name": "New EP",
                            "artists": [{"name": "One"}, {"name": "Two"}],
                            "release_date": "2026-09-14",
                            "release_date_precision": "day",
                            "album_group": "single",
                            "external_urls": {"spotify": "https://open.spotify.com/album/alb1"},
                        }
                    ],
                    "next": None,
                }
            ),
        ]
    )
    albums = make_client(session).artist_albums("a1", include_groups=("single",))
    assert albums[0].artist_line == "One, Two"
    assert albums[0].release_date == "2026-09-14"


def test_local_and_unplayable_tracks_are_dropped():
    session = FakeSession(
        [
            token_response(),
            FakeResponse(
                payload={
                    "items": [
                        {"id": "t1", "uri": "spotify:track:t1", "name": "Good", "artists": []},
                        {"id": "t2", "uri": "spotify:local:x", "name": "Local", "is_local": True},
                        {"name": "No id at all"},
                    ],
                    "next": None,
                }
            ),
        ]
    )
    tracks = make_client(session).album_tracks("alb1", "New EP")
    assert [t.id for t in tracks] == ["t1"]


def test_playlist_additions_are_chunked_at_one_hundred():
    responses = [token_response()] + [FakeResponse(payload={"snapshot_id": "s"})] * 3
    session = FakeSession(responses)
    added = make_client(session).add_tracks_to_playlist(
        "pl1", [f"spotify:track:t{i}" for i in range(250)]
    )
    assert added == 250
    post_calls = [r for r in session.requests if r[0] == "POST" and "playlists" in r[1]]
    assert len(post_calls) == 3  # 100 + 100 + 50


def test_artist_albums_never_asks_for_more_than_ten():
    """The endpoint 400s on limit > 10, so the page size must stay capped."""
    captured = {}

    class RecordingSession(FakeSession):
        def request(self, method, url, **kwargs):
            captured.update(kwargs.get("params") or {})
            return super().request(method, url, **kwargs)

    session = RecordingSession([token_response(), FakeResponse(payload={"items": [], "next": None})])
    make_client(session).artist_albums("a1", include_groups=("album", "single"))
    assert captured["limit"] <= 10


def test_a_long_rate_limit_aborts_instead_of_retrying(monkeypatch):
    """A multi-hour block must raise at once, not sit in a retry loop."""
    slept = []
    monkeypatch.setattr("spotify_release_bot.spotify_client.time.sleep", slept.append)
    session = FakeSession(
        [
            token_response(),
            FakeResponse(status_code=429, headers={"Retry-After": "86393"}, payload={}),
        ]
    )
    with pytest.raises(SpotifyRateLimited, match="23h"):
        make_client(session).artist_albums("a1", include_groups=("single",))
    assert slept == []  # nothing waited on: it gave up immediately


def test_a_short_rate_limit_is_still_retried(monkeypatch):
    slept = []
    monkeypatch.setattr("spotify_release_bot.spotify_client.time.sleep", slept.append)
    session = FakeSession(
        [
            token_response(),
            FakeResponse(status_code=429, headers={"Retry-After": "3"}, payload={}),
            FakeResponse(payload={"items": [], "next": None}),
        ]
    )
    assert make_client(session).artist_albums("a1", include_groups=("single",)) == []
    assert slept == [4]  # Retry-After + 1 second of margin


def test_requests_are_paced_when_a_delay_is_configured(monkeypatch):
    """Consecutive calls must not be fired back to back."""
    slept = []
    monkeypatch.setattr("spotify_release_bot.spotify_client.time.sleep", slept.append)
    monkeypatch.setattr(
        "spotify_release_bot.spotify_client.time.monotonic", lambda: 1000.0
    )
    session = FakeSession(
        [
            token_response(),
            FakeResponse(payload={"items": [], "next": None}),
            FakeResponse(payload={"items": [], "next": None}),
        ]
    )
    client = SpotifyClient(
        "id", "secret", "refresh", market="NL", session=session, request_delay=0.5
    )
    client.artist_albums("a1", include_groups=("single",))
    client.artist_albums("a2", include_groups=("single",))
    assert any(abs(s - 0.5) < 0.01 for s in slept)


def test_playlist_items_uses_the_current_endpoint():
    """/tracks was removed in Feb 2026; Development apps get a bare 403 from it."""
    seen = {}

    class RecordingSession(FakeSession):
        def request(self, method, url, **kwargs):
            seen.setdefault("urls", []).append(url)
            return super().request(method, url, **kwargs)

    session = RecordingSession(
        [token_response(), FakeResponse(payload={"items": [], "next": None})]
    )
    make_client(session).playlist_track_ids("pl1")
    assert any(u.endswith("/playlists/pl1/items") for u in seen["urls"])
    assert not any("/playlists/pl1/tracks" in u for u in seen["urls"])


def test_playlist_items_reads_the_renamed_item_field():
    session = FakeSession(
        [
            token_response(),
            FakeResponse(
                payload={
                    "items": [
                        {"item": {"id": "t1"}},
                        {"item": {"id": "t2"}},
                    ],
                    "next": None,
                }
            ),
        ]
    )
    assert make_client(session).playlist_track_ids("pl1") == {"t1", "t2"}


def test_playlist_items_still_accepts_the_old_track_field():
    """Tolerate the pre-migration spelling rather than silently reading nothing."""
    session = FakeSession(
        [
            token_response(),
            FakeResponse(payload={"items": [{"track": {"id": "t1"}}], "next": None}),
        ]
    )
    assert make_client(session).playlist_track_ids("pl1") == {"t1"}


def test_playlist_items_skips_empty_entries():
    """Removed or unavailable entries come back as nulls and must not crash."""
    session = FakeSession(
        [
            token_response(),
            FakeResponse(
                payload={
                    "items": [{"item": None}, {}, {"item": {"id": "t1"}}],
                    "next": None,
                }
            ),
        ]
    )
    assert make_client(session).playlist_track_ids("pl1") == {"t1"}


def test_adding_tracks_posts_to_the_items_endpoint():
    session = FakeSession([token_response(), FakeResponse(payload={"snapshot_id": "s"})])
    make_client(session).add_tracks_to_playlist("pl1", ["spotify:track:t1"])
    posts = [r for r in session.requests if r[0] == "POST" and "playlists" in r[1]]
    assert posts and posts[0][1].endswith("/playlists/pl1/items")


def test_followed_artists_stops_paging_once_the_limit_is_reached():
    """A capped test run must not pay for pages it will discard."""
    session = FakeSession(
        [
            token_response(),
            FakeResponse(
                payload={
                    "artists": {
                        "items": [{"id": f"a{i}", "name": f"Artist {i}"} for i in range(50)],
                        "next": "https://api.spotify.com/v1/me/following?after=a49",
                        "cursors": {"after": "a49"},
                    }
                }
            ),
            # No second page queued: requesting one would raise.
        ]
    )
    artists = make_client(session).followed_artists(limit=10)
    assert len(artists) == 10

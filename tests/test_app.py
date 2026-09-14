"""End-to-end test of one nightly run, with fake Spotify and WhatsApp clients.

No network is involved: the fakes stand in for the real API wrappers, which is
enough to prove that the pieces are wired together correctly.
"""

from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

from conftest import make_album, make_track
from spotify_release_bot.app import run_once
from spotify_release_bot.config import AppConfig, SpotifyConfig, WhatsAppConfig
from spotify_release_bot.models import Artist
from spotify_release_bot.state import StateStore

TODAY = date(2026, 9, 14)


class FakeSpotify:
    def __init__(self, artists, albums_by_artist, tracks_by_album, playlist_ids=None):
        self._artists = artists
        self._albums_by_artist = albums_by_artist
        self._tracks_by_album = tracks_by_album
        self._playlist_ids = playlist_ids or set()
        self.added_uris: list[str] = []

    def followed_artists(self):
        return self._artists

    def artist_albums(self, artist_id, include_groups, max_pages=1):
        return self._albums_by_artist.get(artist_id, [])

    def album_tracks(self, album_id, album_name):
        return self._tracks_by_album.get(album_id, [])

    def playlist_track_ids(self, playlist_id):
        return set(self._playlist_ids)

    def add_tracks_to_playlist(self, playlist_id, uris):
        self.added_uris.extend(uris)
        return len(uris)


class FakeWhatsApp:
    def __init__(self):
        self.sent: list[str] = []

    def send_all(self, bodies, max_messages):
        self.sent.extend(bodies[:max_messages])
        return len(self.sent)


def make_config(tmp_path, dry_run=False, message_mode="per_track", template_name=""):
    return AppConfig(
        spotify=SpotifyConfig(
            client_id="id",
            client_secret="secret",
            refresh_token="refresh",
            playlist_id="playlist123",
            market="NL",
            include_groups=("album", "single"),
            album_pages_per_artist=1,
        ),
        whatsapp=WhatsAppConfig(
            enabled=True,
            access_token="token",
            phone_number_id="123",
            recipient="31612345678",
            api_version="v21.0",
            message_mode=message_mode,
            max_messages_per_run=25,
            template_name=template_name,
            template_language="en",
        ),
        run_at=time(0, 3),
        timezone=ZoneInfo("Europe/Amsterdam"),
        lookback_days=2,
        state_path=str(tmp_path / "state.json"),
        dry_run=dry_run,
        log_level="INFO",
        state_retention_days=180,
    )


def test_new_release_is_added_and_announced(tmp_path):
    spotify = FakeSpotify(
        artists=[Artist("art1", "Test Artist")],
        albums_by_artist={"art1": [make_album("alb1", release_date="2026-09-14")]},
        tracks_by_album={"alb1": [make_track("trk1"), make_track("trk2")]},
    )
    whatsapp = FakeWhatsApp()
    config = make_config(tmp_path)
    state = StateStore(config.state_path)

    result = run_once(config, spotify, whatsapp, state, today=TODAY)

    assert spotify.added_uris == ["spotify:track:trk1", "spotify:track:trk2"]
    assert len(whatsapp.sent) == 2
    assert len(result.tracks_added) == 2


def test_second_run_on_the_same_day_stays_quiet(tmp_path):
    spotify = FakeSpotify(
        artists=[Artist("art1", "Test Artist")],
        albums_by_artist={"art1": [make_album("alb1", release_date="2026-09-14")]},
        tracks_by_album={"alb1": [make_track("trk1")]},
    )
    whatsapp = FakeWhatsApp()
    config = make_config(tmp_path)

    run_once(config, spotify, whatsapp, StateStore(config.state_path), today=TODAY)
    second = run_once(config, spotify, whatsapp, StateStore(config.state_path), today=TODAY)

    assert second.new_albums == []
    assert len(whatsapp.sent) == 1  # nothing sent the second time


def test_tracks_already_in_the_playlist_are_not_added_again(tmp_path):
    spotify = FakeSpotify(
        artists=[Artist("art1", "Test Artist")],
        albums_by_artist={"art1": [make_album("alb1", release_date="2026-09-14")]},
        tracks_by_album={"alb1": [make_track("trk1"), make_track("trk2")]},
        playlist_ids={"trk1"},
    )
    whatsapp = FakeWhatsApp()
    config = make_config(tmp_path)

    result = run_once(config, spotify, whatsapp, StateStore(config.state_path), today=TODAY)

    assert spotify.added_uris == ["spotify:track:trk2"]
    assert result.tracks_skipped_as_duplicate == 1
    assert len(whatsapp.sent) == 1


def test_old_releases_are_ignored(tmp_path):
    spotify = FakeSpotify(
        artists=[Artist("art1", "Test Artist")],
        albums_by_artist={"art1": [make_album("alb1", release_date="2019-01-01")]},
        tracks_by_album={"alb1": [make_track("trk1")]},
    )
    whatsapp = FakeWhatsApp()
    config = make_config(tmp_path)

    result = run_once(config, spotify, whatsapp, StateStore(config.state_path), today=TODAY)

    assert result.new_albums == []
    assert spotify.added_uris == []
    assert whatsapp.sent == []


def test_dry_run_changes_nothing(tmp_path):
    spotify = FakeSpotify(
        artists=[Artist("art1", "Test Artist")],
        albums_by_artist={"art1": [make_album("alb1", release_date="2026-09-14")]},
        tracks_by_album={"alb1": [make_track("trk1")]},
    )
    whatsapp = FakeWhatsApp()
    config = make_config(tmp_path, dry_run=True)

    result = run_once(config, spotify, whatsapp, StateStore(config.state_path), today=TODAY)

    assert spotify.added_uris == []
    assert whatsapp.sent == []
    assert len(result.tracks_added) == 1  # reported, not performed
    assert not (tmp_path / "state.json").exists()


def test_summary_mode_sends_one_message(tmp_path):
    spotify = FakeSpotify(
        artists=[Artist("art1", "Test Artist")],
        albums_by_artist={"art1": [make_album("alb1", release_date="2026-09-14")]},
        tracks_by_album={"alb1": [make_track("trk1"), make_track("trk2")]},
    )
    whatsapp = FakeWhatsApp()
    config = make_config(tmp_path, message_mode="summary")

    run_once(config, spotify, whatsapp, StateStore(config.state_path), today=TODAY)

    assert len(whatsapp.sent) == 1


def test_following_nobody_is_handled_gracefully(tmp_path):
    spotify = FakeSpotify(artists=[], albums_by_artist={}, tracks_by_album={})
    config = make_config(tmp_path)
    result = run_once(config, spotify, FakeWhatsApp(), StateStore(config.state_path), today=TODAY)
    assert result.artists_checked == 0
    assert result.new_albums == []


def test_template_mode_sends_single_line_parameters(tmp_path):
    """With a template configured, each message must be one clean line."""
    spotify = FakeSpotify(
        artists=[Artist("art1", "Test Artist")],
        albums_by_artist={"art1": [make_album("alb1", release_date="2026-09-14")]},
        tracks_by_album={"alb1": [make_track("trk1")]},
    )
    whatsapp = FakeWhatsApp()
    config = make_config(tmp_path, template_name="new_release")

    run_once(config, spotify, whatsapp, StateStore(config.state_path), today=TODAY)

    assert len(whatsapp.sent) == 1
    assert "\n" not in whatsapp.sent[0]
    assert "https://open.spotify.com/track/trk1" in whatsapp.sent[0]

"""Orchestration: one nightly run, start to finish.

The flow is:

    followed artists -> their recent albums -> filter to genuinely new ones
        -> collect the tracks -> add to the playlist -> announce on WhatsApp
        -> remember what we handled

Every step is delegated to a module that does one thing, so this file stays
readable as a description of *what* happens rather than *how*.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from .config import AppConfig
from .models import Album, Track
from .messages import build_messages, build_template_parameters
from .release_finder import select_new_albums
from .spotify_client import SpotifyClient, SpotifyError, SpotifyRateLimited
from .state import StateStore
from .whatsapp_client import WhatsAppClient

_LOG = logging.getLogger(__name__)


@dataclass
class RunResult:
    """What a single run did, for logging and for the tests."""

    artists_checked: int = 0
    new_albums: list[Album] = field(default_factory=list)
    tracks_added: list[Track] = field(default_factory=list)
    tracks_skipped_as_duplicate: int = 0
    whatsapp_messages_sent: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"checked {self.artists_checked} artists, "
            f"{len(self.new_albums)} new release(s), "
            f"{len(self.tracks_added)} track(s) added, "
            f"{self.tracks_skipped_as_duplicate} duplicate(s) skipped, "
            f"{self.whatsapp_messages_sent} WhatsApp message(s) sent"
        )


def run_once(
    config: AppConfig,
    spotify: SpotifyClient,
    whatsapp: WhatsAppClient | None,
    state: StateStore,
    today: date | None = None,
) -> RunResult:
    """Perform one complete check. Returns a summary of what happened."""
    today = today or datetime.now(tz=config.timezone).date()
    result = RunResult()

    state.load()
    seen_album_ids = state.seen_album_ids()

    artists = spotify.followed_artists()
    if not artists:
        _LOG.warning("You do not follow any artists, so there is nothing to check")
        return result

    if config.spotify.max_artists:
        # Checking every artist costs one request each, which is what trips
        # Spotify's rate limiter. A cap makes a test run cheap.
        artists = artists[: config.spotify.max_artists]
        _LOG.info("Only checking the first %d artists (SPOTIFY_MAX_ARTISTS)", len(artists))
    result.artists_checked = len(artists)

    # 1. Gather candidate releases from every followed artist.
    candidates: list[Album] = []
    for artist in artists:
        try:
            candidates.extend(
                spotify.artist_albums(
                    artist.id,
                    include_groups=config.spotify.include_groups,
                    max_pages=config.spotify.album_pages_per_artist,
                )
            )
        except SpotifyRateLimited:
            # Every following request would fail too, and each one risks
            # extending the block, so stop rather than working through the
            # rest of the list.
            _LOG.error("Stopping this run: Spotify has rate limited the app")
            raise
        except SpotifyError as exc:
            # One unreachable artist must not sink the whole run.
            message = f"Could not load albums for {artist.name}: {exc}"
            _LOG.error(message)
            result.errors.append(message)

    # 2. Reduce them to what is actually new to us.
    new_albums = select_new_albums(candidates, seen_album_ids, today, config.lookback_days)
    result.new_albums = new_albums
    _LOG.info(
        "Found %d new release(s) out of %d candidate album(s)", len(new_albums), len(candidates)
    )
    if not new_albums:
        _finish(state, today, config, [])
        return result

    for album in new_albums:
        _LOG.info("New: %s - %s (%s)", album.artist_line, album.name, album.release_date)

    # 3. Expand each release into its individual tracks.
    tracks: list[Track] = []
    handled_album_ids: list[str] = []
    for album in new_albums:
        try:
            album_tracks = spotify.album_tracks(album.id, album.name)
        except SpotifyError as exc:
            message = f"Could not load tracks for album {album.name}: {exc}"
            _LOG.error(message)
            result.errors.append(message)
            continue  # not marked as handled, so the next run retries it
        tracks.extend(album_tracks)
        handled_album_ids.append(album.id)

    # 4. Drop anything already sitting in the playlist.
    try:
        existing = spotify.playlist_track_ids(config.spotify.playlist_id)
    except SpotifyError as exc:
        message = f"Could not read the target playlist: {exc}"
        _LOG.error(message)
        result.errors.append(message)
        existing = set()

    fresh_tracks: list[Track] = []
    seen_in_this_run: set[str] = set()
    for track in tracks:
        if track.id in existing or track.id in seen_in_this_run:
            result.tracks_skipped_as_duplicate += 1
            continue
        seen_in_this_run.add(track.id)
        fresh_tracks.append(track)

    if not fresh_tracks:
        _LOG.info("Every track from the new releases was already in the playlist")
        _finish(state, today, config, handled_album_ids)
        return result

    # 5. Add them to the playlist.
    if config.dry_run:
        _LOG.info("[dry run] Would add %d track(s) to the playlist", len(fresh_tracks))
        result.tracks_added = fresh_tracks
    else:
        try:
            spotify.add_tracks_to_playlist(
                config.spotify.playlist_id, [t.uri for t in fresh_tracks]
            )
            result.tracks_added = fresh_tracks
        except SpotifyError as exc:
            message = f"Could not add tracks to the playlist: {exc}"
            _LOG.error(message)
            result.errors.append(message)
            # Nothing was added, so do not remember these albums as handled:
            # a later run should get another chance.
            return result

    # 6. Announce them on WhatsApp.
    if whatsapp is not None and config.whatsapp.enabled:
        # A template variable has stricter formatting rules than a text body,
        # so the wording is built differently for each.
        if config.whatsapp.template_name:
            bodies = build_template_parameters(fresh_tracks, config.whatsapp.message_mode)
        else:
            bodies = build_messages(fresh_tracks, config.whatsapp.message_mode)
        if config.dry_run:
            _LOG.info("[dry run] Would send %d WhatsApp message(s):", len(bodies))
            for body in bodies:
                _LOG.info("[dry run] ---\n%s", body)
            result.whatsapp_messages_sent = len(bodies)
        else:
            result.whatsapp_messages_sent = whatsapp.send_all(
                bodies, config.whatsapp.max_messages_per_run
            )
    else:
        _LOG.info("WhatsApp is disabled; skipping notifications")

    # 7. Remember what we handled, so tomorrow's run stays quiet about it.
    _finish(state, today, config, handled_album_ids)
    return result


def _finish(state: StateStore, today: date, config: AppConfig, album_ids: list[str]) -> None:
    """Record handled albums and persist the state file."""
    if config.dry_run:
        _LOG.info("[dry run] Not writing the state file")
        return
    state.mark_handled(album_ids, today)
    removed = state.prune(today, config.state_retention_days)
    if removed:
        _LOG.debug("Pruned %d stale state entries", removed)
    try:
        state.save(datetime.now(tz=config.timezone).isoformat(timespec="seconds"))
    except OSError as exc:
        _LOG.error("Could not write the state file at %s: %s", state.path, exc)

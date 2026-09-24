"""Remembers which albums were already handled, between container restarts.

Stored as a small JSON file on a mounted volume. Deliberately boring: a
database would be overkill for a few thousand album IDs, and a plain file is
easy to inspect or delete when you want the bot to start fresh.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, timedelta
from typing import Iterable

_LOG = logging.getLogger(__name__)

_CURRENT_VERSION = 1


class StateStore:
    """Reads and writes the seen-albums file.

    The in-memory view is a mapping of album id -> the date we first handled it.
    Keeping the date (instead of a bare set) lets us prune entries that are far
    outside any lookback window, so the file cannot grow forever.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._seen: dict[str, str] = {}
        self._last_run: str | None = None
        self._checked_artists: set[str] = set()

    @property
    def path(self) -> str:
        return self._path

    @property
    def last_run(self) -> str | None:
        return self._last_run

    def seen_album_ids(self) -> set[str]:
        return set(self._seen)

    def load(self) -> None:
        """Load the file, tolerating a missing or corrupt one.

        A corrupt state file must never stop the bot: the worst case of
        starting empty is that a few already-posted albums get posted again,
        which is far better than the bot dying every night.
        """
        if not os.path.exists(self._path):
            _LOG.info("No state file at %s yet; starting with an empty history", self._path)
            return
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            _LOG.warning("Could not read state file %s (%s); starting empty", self._path, exc)
            return

        if not isinstance(payload, dict):
            _LOG.warning("State file %s has an unexpected shape; starting empty", self._path)
            return

        seen = payload.get("seen_albums", {})
        if isinstance(seen, dict):
            self._seen = {str(k): str(v) for k, v in seen.items()}
        elif isinstance(seen, list):  # tolerate an older, simpler format
            self._seen = {str(album_id): "1970-01-01" for album_id in seen}
        else:
            _LOG.warning("State file %s has an unreadable seen_albums field", self._path)

        checked = payload.get("checked_artists", [])
        if isinstance(checked, list):
            self._checked_artists = {str(a) for a in checked}

        last_run = payload.get("last_run")
        self._last_run = str(last_run) if last_run else None
        _LOG.info("Loaded %d previously handled albums from %s", len(self._seen), self._path)

    def mark_handled(self, album_ids: Iterable[str], today: date) -> None:
        stamp = today.isoformat()
        for album_id in album_ids:
            self._seen.setdefault(album_id, stamp)

    def checked_artists(self) -> set[str]:
        """Artists already visited in the current rotation cycle."""
        return set(self._checked_artists)

    def mark_artists_checked(self, artist_ids: Iterable[str]) -> None:
        self._checked_artists.update(artist_ids)

    def start_new_artist_cycle(self) -> None:
        """Everyone has been checked; begin again from the top."""
        self._checked_artists = set()

    def prune(self, today: date, retention_days: int) -> int:
        """Drop entries older than the retention window. Returns how many went."""
        cutoff = today - timedelta(days=retention_days)
        keep: dict[str, str] = {}
        for album_id, stamp in self._seen.items():
            try:
                handled_on = date.fromisoformat(stamp)
            except ValueError:
                handled_on = today  # unreadable stamp: keep it, refresh the date
            if handled_on >= cutoff:
                keep[album_id] = handled_on.isoformat()
        removed = len(self._seen) - len(keep)
        self._seen = keep
        return removed

    def save(self, last_run_iso: str) -> None:
        """Write the file atomically so a crash cannot leave half a file behind.

        We write to a temporary file in the same directory and then rename it;
        on POSIX a rename within one filesystem is atomic, so readers either see
        the old complete file or the new complete file, never a truncated one.
        """
        self._last_run = last_run_iso
        payload = {
            "version": _CURRENT_VERSION,
            "last_run": last_run_iso,
            "seen_albums": self._seen,
            "checked_artists": sorted(self._checked_artists),
        }
        directory = os.path.dirname(os.path.abspath(self._path)) or "."
        os.makedirs(directory, exist_ok=True)

        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, delete=False, suffix=".tmp"
        )
        try:
            with handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self._path)
        except OSError:
            if os.path.exists(handle.name):
                os.unlink(handle.name)
            raise
        _LOG.debug("Saved state (%d albums) to %s", len(self._seen), self._path)

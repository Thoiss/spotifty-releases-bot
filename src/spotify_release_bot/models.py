"""Plain data objects shared between the Spotify, WhatsApp and logic layers.

Everything here is immutable (``frozen=True``) so a value can be passed around
without any layer being able to mutate another layer's data.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Artist:
    """An artist the user follows on Spotify."""

    id: str
    name: str


@dataclass(frozen=True)
class Album:
    """An album, single or EP as returned by the Spotify API.

    ``release_date`` is kept as the raw Spotify string ("2026-09-14",
    "2026-09" or "2026") together with ``release_date_precision``, because
    Spotify does not always know the exact day. Use
    :func:`spotify_release_bot.release_finder.parse_release_date` to turn the
    pair into a real date.
    """

    id: str
    name: str
    artists: tuple[str, ...]
    release_date: str
    release_date_precision: str
    album_group: str
    url: str

    @property
    def artist_line(self) -> str:
        return ", ".join(self.artists)


@dataclass(frozen=True)
class Track:
    """A single track that will be added to the playlist and announced."""

    id: str
    uri: str
    name: str
    artists: tuple[str, ...]
    url: str
    album_name: str

    @property
    def artist_line(self) -> str:
        return ", ".join(self.artists)

"""Shared test helpers."""

from __future__ import annotations

from spotify_release_bot.models import Album, Track


def make_album(
    album_id: str = "alb1",
    name: str = "Test Album",
    release_date: str = "2026-09-14",
    precision: str = "day",
    artists: tuple[str, ...] = ("Test Artist",),
) -> Album:
    return Album(
        id=album_id,
        name=name,
        artists=artists,
        release_date=release_date,
        release_date_precision=precision,
        album_group="single",
        url=f"https://open.spotify.com/album/{album_id}",
    )


def make_track(
    track_id: str = "trk1",
    name: str = "Test Song",
    artists: tuple[str, ...] = ("Test Artist",),
    album_name: str = "Test Album",
) -> Track:
    return Track(
        id=track_id,
        uri=f"spotify:track:{track_id}",
        name=name,
        artists=artists,
        url=f"https://open.spotify.com/track/{track_id}",
        album_name=album_name,
    )

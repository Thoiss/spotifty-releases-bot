"""The decision logic: which albums count as 'new' and should be processed.

This module deliberately contains no network code at all. It takes albums that
somebody else fetched and answers the question "is this new to us?". That makes
it the part you can unit-test without touching Spotify.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Sequence

from .models import Album


class UnparsableReleaseDate(ValueError):
    """Raised when Spotify hands us a release date we cannot interpret."""


def parse_release_date(release_date: str, precision: str) -> date:
    """Turn Spotify's (date string, precision) pair into a real ``date``.

    Spotify does not always know the exact release day. The precision field
    tells us how much of the string is meaningful:

    * ``"day"``   -> "2026-09-14"  -> 2026-09-14
    * ``"month"`` -> "2026-09"     -> 2026-09-01 (first of that month)
    * ``"year"``  -> "2026"        -> 2026-01-01 (first of that year)

    Rounding down is the safe direction: a vaguely-dated album will look older
    than today, so it is simply skipped instead of being announced by mistake.
    """
    text = (release_date or "").strip()
    if not text:
        raise UnparsableReleaseDate("empty release_date")

    parts = text.split("-")
    try:
        if precision == "year" or len(parts) == 1:
            return date(int(parts[0]), 1, 1)
        if precision == "month" or len(parts) == 2:
            return date(int(parts[0]), int(parts[1]), 1)
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError) as exc:
        raise UnparsableReleaseDate(
            f"cannot parse release date {release_date!r} with precision {precision!r}"
        ) from exc


def is_recent(album: Album, today: date, lookback_days: int) -> bool:
    """True when the album was released inside the lookback window.

    The window is ``[today - lookback_days, today]``. A couple of days of slack
    matters because Spotify publishes per-region at midnight local time, so an
    album released "today" somewhere can still show up a day late for us. Future
    dates are rejected: pre-release placeholders should not be announced yet.
    """
    if lookback_days < 0:
        raise ValueError("lookback_days may not be negative")
    try:
        released = parse_release_date(album.release_date, album.release_date_precision)
    except UnparsableReleaseDate:
        return False
    oldest_allowed = today - timedelta(days=lookback_days)
    return oldest_allowed <= released <= today


def select_new_albums(
    albums: Iterable[Album],
    seen_album_ids: Sequence[str] | set[str],
    today: date,
    lookback_days: int,
) -> list[Album]:
    """Filter albums down to the ones that are recent AND not handled before.

    Two independent guards, on purpose:

    1. the date window stops us from re-importing an artist's back catalogue;
    2. the seen-set stops us from re-announcing something we already posted,
       even if the bot runs twice on the same day.

    Duplicates inside ``albums`` are collapsed, because several followed artists
    can appear on the same collaboration album. The result is sorted newest
    first, then by name, so runs are reproducible.
    """
    already_seen = set(seen_album_ids)
    picked: dict[str, Album] = {}

    for album in albums:
        if album.id in already_seen or album.id in picked:
            continue
        if not is_recent(album, today, lookback_days):
            continue
        picked[album.id] = album

    def sort_key(album: Album) -> tuple[date, str]:
        return (
            parse_release_date(album.release_date, album.release_date_precision),
            album.name.lower(),
        )

    return sorted(picked.values(), key=sort_key, reverse=True)

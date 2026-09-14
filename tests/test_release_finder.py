"""Tests for the 'is this release new?' logic - the part that decides whether
a song ends up in your playlist, so the part most worth testing."""

from __future__ import annotations

from datetime import date

import pytest

from conftest import make_album
from spotify_release_bot.release_finder import (
    UnparsableReleaseDate,
    is_recent,
    parse_release_date,
    select_new_albums,
)

TODAY = date(2026, 9, 14)


class TestParseReleaseDate:
    def test_full_date(self):
        assert parse_release_date("2026-09-14", "day") == date(2026, 9, 14)

    def test_month_precision_rounds_to_first_of_month(self):
        assert parse_release_date("2026-09", "month") == date(2026, 9, 1)

    def test_year_precision_rounds_to_first_of_year(self):
        assert parse_release_date("2026", "year") == date(2026, 1, 1)

    def test_empty_date_is_rejected(self):
        with pytest.raises(UnparsableReleaseDate):
            parse_release_date("", "day")

    def test_garbage_date_is_rejected(self):
        with pytest.raises(UnparsableReleaseDate):
            parse_release_date("not-a-date", "day")


class TestIsRecent:
    def test_released_today_is_recent(self):
        assert is_recent(make_album(release_date="2026-09-14"), TODAY, lookback_days=2)

    def test_released_inside_window_is_recent(self):
        assert is_recent(make_album(release_date="2026-09-12"), TODAY, lookback_days=2)

    def test_released_just_outside_window_is_not(self):
        assert not is_recent(make_album(release_date="2026-09-11"), TODAY, lookback_days=2)

    def test_future_release_is_not_announced_yet(self):
        assert not is_recent(make_album(release_date="2026-12-01"), TODAY, lookback_days=2)

    def test_unparsable_date_is_skipped_rather_than_crashing(self):
        album = make_album(release_date="???", precision="day")
        assert not is_recent(album, TODAY, lookback_days=2)

    def test_vague_year_only_date_is_skipped(self):
        # "2026" becomes 2026-01-01, far outside the window, so an artist's
        # loosely dated back catalogue never floods the playlist.
        album = make_album(release_date="2026", precision="year")
        assert not is_recent(album, TODAY, lookback_days=2)


class TestSelectNewAlbums:
    def test_picks_only_recent_and_unseen(self):
        albums = [
            make_album("new1", release_date="2026-09-14"),
            make_album("old1", release_date="2020-01-01"),
            make_album("seen1", release_date="2026-09-14"),
        ]
        result = select_new_albums(albums, {"seen1"}, TODAY, lookback_days=2)
        assert [a.id for a in result] == ["new1"]

    def test_collapses_the_same_album_seen_via_two_artists(self):
        # A collaboration shows up once per followed artist; it must be
        # imported and announced only once.
        albums = [
            make_album("collab", release_date="2026-09-14"),
            make_album("collab", release_date="2026-09-14"),
        ]
        result = select_new_albums(albums, set(), TODAY, lookback_days=2)
        assert len(result) == 1

    def test_sorted_newest_first(self):
        albums = [
            make_album("a", name="A", release_date="2026-09-12"),
            make_album("b", name="B", release_date="2026-09-14"),
            make_album("c", name="C", release_date="2026-09-13"),
        ]
        result = select_new_albums(albums, set(), TODAY, lookback_days=5)
        assert [a.id for a in result] == ["b", "c", "a"]

    def test_empty_input_gives_empty_output(self):
        assert select_new_albums([], set(), TODAY, lookback_days=2) == []

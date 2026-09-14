"""Tests for the seen-albums file."""

from __future__ import annotations

import json
from datetime import date

from spotify_release_bot.state import StateStore

TODAY = date(2026, 9, 14)


def test_missing_file_starts_empty(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    store.load()
    assert store.seen_album_ids() == set()


def test_round_trip(tmp_path):
    path = str(tmp_path / "state.json")
    store = StateStore(path)
    store.load()
    store.mark_handled(["a", "b"], TODAY)
    store.save("2026-09-14T00:03:00+02:00")

    reloaded = StateStore(path)
    reloaded.load()
    assert reloaded.seen_album_ids() == {"a", "b"}
    assert reloaded.last_run == "2026-09-14T00:03:00+02:00"


def test_corrupt_file_does_not_crash_the_bot(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = StateStore(str(path))
    store.load()  # must not raise
    assert store.seen_album_ids() == set()


def test_legacy_list_format_is_still_readable(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"seen_albums": ["x", "y"]}), encoding="utf-8")
    store = StateStore(str(path))
    store.load()
    assert store.seen_album_ids() == {"x", "y"}


def test_prune_drops_entries_outside_the_retention_window(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    store.load()
    store.mark_handled(["old"], date(2020, 1, 1))
    store.mark_handled(["new"], TODAY)
    removed = store.prune(TODAY, retention_days=180)
    assert removed == 1
    assert store.seen_album_ids() == {"new"}


def test_mark_handled_keeps_the_first_date(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    store.load()
    store.mark_handled(["a"], date(2026, 1, 1))
    store.mark_handled(["a"], TODAY)
    store.save("now")
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert raw["seen_albums"]["a"] == "2026-01-01"


def test_save_creates_the_directory_if_needed(tmp_path):
    path = tmp_path / "nested" / "dir" / "state.json"
    store = StateStore(str(path))
    store.load()
    store.mark_handled(["a"], TODAY)
    store.save("now")
    assert path.exists()

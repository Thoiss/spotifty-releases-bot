"""Tests for the WhatsApp message wording."""

from __future__ import annotations

from conftest import make_track
from spotify_release_bot.messages import (
    MAX_BODY_LENGTH,
    MAX_TEMPLATE_PARAMETER_LENGTH,
    build_messages,
    build_template_parameters,
    format_summary_message,
    format_template_parameter,
    format_track_message,
)


def test_track_message_contains_artist_title_and_link():
    track = make_track(name="Midnight", artists=("Artist One",))
    body = format_track_message(track)
    assert "Artist One" in body
    assert "Midnight" in body
    assert "https://open.spotify.com/track/trk1" in body


def test_multiple_artists_are_joined():
    track = make_track(artists=("A", "B"))
    assert "A, B" in format_track_message(track)


def test_per_track_mode_gives_one_message_per_song():
    tracks = [make_track("t1"), make_track("t2"), make_track("t3")]
    assert len(build_messages(tracks, "per_track")) == 3


def test_summary_mode_gives_a_single_message_with_every_link():
    tracks = [make_track("t1"), make_track("t2")]
    bodies = build_messages(tracks, "summary")
    assert len(bodies) == 1
    assert "t1" in bodies[0] and "t2" in bodies[0]


def test_no_tracks_means_no_messages():
    assert build_messages([], "per_track") == []
    assert build_messages([], "summary") == []


def test_summary_is_truncated_to_the_whatsapp_limit():
    tracks = [make_track(f"t{i}", name="X" * 200) for i in range(100)]
    body = format_summary_message(tracks)
    assert len(body) <= MAX_BODY_LENGTH


def test_template_parameter_is_a_single_line():
    # Meta rejects newlines and tabs inside a template variable.
    track = make_track(name="Song\nWith\tWhitespace")
    param = format_template_parameter(track)
    assert "\n" not in param and "\t" not in param
    assert "    " not in param


def test_template_parameter_respects_the_length_limit():
    track = make_track(name="Y" * 5000)
    assert len(format_template_parameter(track)) <= MAX_TEMPLATE_PARAMETER_LENGTH


def test_template_summary_collapses_everything_onto_one_line():
    tracks = [make_track("t1"), make_track("t2")]
    params = build_template_parameters(tracks, "summary")
    assert len(params) == 1
    assert "\n" not in params[0]
    assert "t1" in params[0] and "t2" in params[0]


def test_template_per_track_gives_one_parameter_per_song():
    tracks = [make_track("t1"), make_track("t2"), make_track("t3")]
    assert len(build_template_parameters(tracks, "per_track")) == 3

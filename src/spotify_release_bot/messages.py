"""Turns tracks into the text that gets sent over WhatsApp.

Kept separate from the sender so the wording can be unit-tested without any
network access, and changed without touching the API code.
"""

from __future__ import annotations

from typing import Sequence

from .models import Track

# WhatsApp rejects a text body longer than this.
MAX_BODY_LENGTH = 4096

# A template *parameter* is far more restricted than a text body: Meta rejects
# newlines, tabs and runs of more than four spaces inside one.
MAX_TEMPLATE_PARAMETER_LENGTH = 1024


def format_track_message(track: Track) -> str:
    """One message per song, so WhatsApp renders a link preview for each."""
    return _truncate(f"🎵 {track.artist_line} - {track.name}\n{track.url}")


def format_summary_message(tracks: Sequence[Track]) -> str:
    """All songs in a single message, for when you do not want 20 notifications.

    Only the first link gets a preview in WhatsApp, which is exactly why
    ``per_track`` is the default mode.
    """
    if not tracks:
        return ""
    header = f"🎧 {len(tracks)} new release{'s' if len(tracks) != 1 else ''} today:"
    lines = [f"• {t.artist_line} - {t.name}\n{t.url}" for t in tracks]
    return _truncate("\n\n".join([header, *lines]))


def build_messages(tracks: Sequence[Track], mode: str) -> list[str]:
    """Produce the list of message bodies to send, according to the mode."""
    if not tracks:
        return []
    if mode == "summary":
        return [format_summary_message(tracks)]
    return [format_track_message(track) for track in tracks]


def _truncate(body: str) -> str:
    if len(body) <= MAX_BODY_LENGTH:
        return body
    return body[: MAX_BODY_LENGTH - 1].rstrip() + "…"


def format_template_parameter(track: Track) -> str:
    """One line of text to drop into a template variable such as {{1}}.

    Template parameters may not contain newlines, tabs or long runs of spaces,
    so everything is squeezed onto a single line here.
    """
    return _single_line(f"{track.artist_line} - {track.name} {track.url}")


def build_template_parameters(tracks: Sequence[Track], mode: str) -> list[str]:
    """The template-message counterpart of :func:`build_messages`."""
    if not tracks:
        return []
    if mode == "summary":
        return [
            _single_line(
                " | ".join(f"{t.artist_line} - {t.name} {t.url}" for t in tracks)
            )
        ]
    return [format_template_parameter(track) for track in tracks]


def _single_line(text: str) -> str:
    """Collapse all whitespace and cut to the template parameter limit."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_TEMPLATE_PARAMETER_LENGTH:
        return collapsed
    return collapsed[: MAX_TEMPLATE_PARAMETER_LENGTH - 1].rstrip() + "\u2026"

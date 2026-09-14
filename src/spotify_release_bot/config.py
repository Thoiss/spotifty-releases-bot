"""Configuration, loaded once from environment variables.

Nothing else in the program reads ``os.environ`` directly: every module gets
the values it needs handed to it. That keeps the modules testable (a test can
build a config object by hand) and keeps all the "where does this setting come
from" knowledge in one file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or malformed."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Required environment variable {name} is missing or empty. "
            f"See .env.example for what it should contain."
        )
    return value


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _boolean(name: str, default: bool) -> bool:
    raw = _optional(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _integer(name: str, default: int, minimum: int = 0) -> int:
    raw = _optional(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _parse_time(name: str, default: str) -> time:
    raw = _optional(name, default) or default
    try:
        hour_text, minute_text = raw.split(":", 1)
        return time(hour=int(hour_text), minute=int(minute_text))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{name} must look like HH:MM (e.g. 00:03), got {raw!r}") from exc


def _parse_timezone(name: str, default: str) -> ZoneInfo:
    raw = _optional(name, default) or default
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"{name} is not a known timezone: {raw!r}") from exc


@dataclass(frozen=True)
class SpotifyConfig:
    """Credentials and tuning knobs for the Spotify side."""

    client_id: str
    client_secret: str
    refresh_token: str
    playlist_id: str
    market: str
    include_groups: tuple[str, ...]
    album_pages_per_artist: int

    @classmethod
    def from_env(cls) -> "SpotifyConfig":
        groups = _optional("SPOTIFY_INCLUDE_GROUPS", "album,single") or "album,single"
        parsed_groups = tuple(g.strip() for g in groups.split(",") if g.strip())
        allowed = {"album", "single", "compilation", "appears_on"}
        unknown = set(parsed_groups) - allowed
        if unknown:
            raise ConfigError(
                f"SPOTIFY_INCLUDE_GROUPS contains unknown values {sorted(unknown)}; "
                f"allowed: {sorted(allowed)}"
            )
        if not parsed_groups:
            raise ConfigError("SPOTIFY_INCLUDE_GROUPS may not be empty")
        return cls(
            client_id=_require("SPOTIFY_CLIENT_ID"),
            client_secret=_require("SPOTIFY_CLIENT_SECRET"),
            refresh_token=_require("SPOTIFY_REFRESH_TOKEN"),
            playlist_id=_require("SPOTIFY_PLAYLIST_ID"),
            market=_optional("SPOTIFY_MARKET", "NL") or "NL",
            include_groups=parsed_groups,
            album_pages_per_artist=_integer("SPOTIFY_ALBUM_PAGES_PER_ARTIST", 1, minimum=1),
        )


@dataclass(frozen=True)
class WhatsAppConfig:
    """Settings for the WhatsApp Cloud API sender.

    Note: the official Cloud API can only send to an individual number, never
    to a group. ``recipient`` is therefore a single phone number in
    international format without '+' or spaces, e.g. 31612345678.

    ``template_name`` is optional but strongly recommended for a bot that fires
    at 00:03: plain text messages are only allowed within 24 hours of your last
    message to the business number, while an approved template may be sent at
    any time. Leave it empty to send plain text.
    """

    enabled: bool
    access_token: str
    phone_number_id: str
    recipient: str
    api_version: str
    message_mode: str
    max_messages_per_run: int
    template_name: str
    template_language: str

    @classmethod
    def from_env(cls) -> "WhatsAppConfig":
        enabled = _boolean("WHATSAPP_ENABLED", True)
        if not enabled:
            return cls(
                enabled=False,
                access_token="",
                phone_number_id="",
                recipient="",
                api_version="v21.0",
                message_mode="per_track",
                max_messages_per_run=0,
                template_name="",
                template_language="en",
            )
        mode = (_optional("WHATSAPP_MESSAGE_MODE", "per_track") or "per_track").lower()
        if mode not in {"per_track", "summary"}:
            raise ConfigError(
                f"WHATSAPP_MESSAGE_MODE must be 'per_track' or 'summary', got {mode!r}"
            )
        recipient = _require("WHATSAPP_RECIPIENT").lstrip("+").replace(" ", "")
        if not recipient.isdigit():
            raise ConfigError(
                "WHATSAPP_RECIPIENT must be digits only, in international format "
                "without '+' (e.g. 31612345678)"
            )
        return cls(
            enabled=True,
            access_token=_require("WHATSAPP_ACCESS_TOKEN"),
            phone_number_id=_require("WHATSAPP_PHONE_NUMBER_ID"),
            recipient=recipient,
            api_version=_optional("WHATSAPP_API_VERSION", "v21.0") or "v21.0",
            message_mode=mode,
            max_messages_per_run=_integer("WHATSAPP_MAX_MESSAGES_PER_RUN", 25, minimum=1),
            template_name=_optional("WHATSAPP_TEMPLATE_NAME"),
            template_language=_optional("WHATSAPP_TEMPLATE_LANGUAGE", "en") or "en",
        )


@dataclass(frozen=True)
class AppConfig:
    """Everything the application needs to run."""

    spotify: SpotifyConfig
    whatsapp: WhatsAppConfig
    run_at: time
    timezone: ZoneInfo
    lookback_days: int
    state_path: str
    dry_run: bool
    log_level: str
    state_retention_days: int = field(default=180)

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            spotify=SpotifyConfig.from_env(),
            whatsapp=WhatsAppConfig.from_env(),
            run_at=_parse_time("RUN_AT", "00:03"),
            timezone=_parse_timezone("TIMEZONE", "Europe/Amsterdam"),
            lookback_days=_integer("LOOKBACK_DAYS", 2, minimum=1),
            state_path=_optional("STATE_PATH", "/data/state.json") or "/data/state.json",
            dry_run=_boolean("DRY_RUN", False),
            log_level=(_optional("LOG_LEVEL", "INFO") or "INFO").upper(),
            state_retention_days=_integer("STATE_RETENTION_DAYS", 180, minimum=1),
        )

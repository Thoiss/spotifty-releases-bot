"""Tests for reading and validating the environment configuration."""

from __future__ import annotations

import pytest

from spotify_release_bot.config import AppConfig, ConfigError, SpotifyConfig, WhatsAppConfig

_MINIMAL_ENV = {
    "SPOTIFY_CLIENT_ID": "id",
    "SPOTIFY_CLIENT_SECRET": "secret",
    "SPOTIFY_REFRESH_TOKEN": "refresh",
    "SPOTIFY_PLAYLIST_ID": "playlist",
    "WHATSAPP_ACCESS_TOKEN": "token",
    "WHATSAPP_PHONE_NUMBER_ID": "123",
    "WHATSAPP_RECIPIENT": "31612345678",
}


@pytest.fixture
def clean_env(monkeypatch):
    for key in list(_MINIMAL_ENV) + [
        "SPOTIFY_MARKET",
        "SPOTIFY_INCLUDE_GROUPS",
        "SPOTIFY_ALBUM_PAGES_PER_ARTIST",
        "WHATSAPP_ENABLED",
        "WHATSAPP_MESSAGE_MODE",
        "WHATSAPP_TEMPLATE_NAME",
        "RUN_AT",
        "TIMEZONE",
        "LOOKBACK_DAYS",
        "DRY_RUN",
        "LOG_LEVEL",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in _MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_defaults_are_sensible(clean_env):
    config = AppConfig.from_env()
    assert config.spotify.request_delay == 1.0   # pacing on by default
    assert config.spotify.max_artists == 0       # 0 means "all of them"
    assert (config.run_at.hour, config.run_at.minute) == (0, 3)
    assert config.timezone.key == "Europe/Amsterdam"
    assert config.lookback_days == 2
    assert config.spotify.include_groups == ("album", "single")
    assert config.dry_run is False


def test_missing_required_value_is_reported_clearly(clean_env):
    clean_env.delenv("SPOTIFY_CLIENT_ID")
    with pytest.raises(ConfigError, match="SPOTIFY_CLIENT_ID"):
        SpotifyConfig.from_env()


def test_bad_run_time_is_rejected(clean_env):
    clean_env.setenv("RUN_AT", "midnight")
    with pytest.raises(ConfigError, match="HH:MM"):
        AppConfig.from_env()


def test_unknown_timezone_is_rejected(clean_env):
    clean_env.setenv("TIMEZONE", "Mars/Olympus_Mons")
    with pytest.raises(ConfigError, match="timezone"):
        AppConfig.from_env()


def test_unknown_include_group_is_rejected(clean_env):
    clean_env.setenv("SPOTIFY_INCLUDE_GROUPS", "album,bootleg")
    with pytest.raises(ConfigError, match="bootleg"):
        SpotifyConfig.from_env()


def test_recipient_must_be_digits_only(clean_env):
    clean_env.setenv("WHATSAPP_RECIPIENT", "06-12345678")
    with pytest.raises(ConfigError, match="digits only"):
        WhatsAppConfig.from_env()


def test_leading_plus_on_the_recipient_is_accepted_and_stripped(clean_env):
    clean_env.setenv("WHATSAPP_RECIPIENT", "+31 6 12345678")
    assert WhatsAppConfig.from_env().recipient == "31612345678"


def test_disabling_whatsapp_skips_its_required_values(clean_env):
    clean_env.setenv("WHATSAPP_ENABLED", "false")
    clean_env.delenv("WHATSAPP_ACCESS_TOKEN")
    config = WhatsAppConfig.from_env()
    assert config.enabled is False


def test_unknown_message_mode_is_rejected(clean_env):
    clean_env.setenv("WHATSAPP_MESSAGE_MODE", "carrier_pigeon")
    with pytest.raises(ConfigError, match="per_track"):
        WhatsAppConfig.from_env()

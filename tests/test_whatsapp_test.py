"""Tests for the whatsapp-test helper."""

from __future__ import annotations

from spotify_release_bot.config import WhatsAppConfig
from spotify_release_bot.whatsapp_test import _ERROR_HELP, _redact, send_test_message


def test_token_is_redacted_but_still_recognisable():
    redacted = _redact("EAAj7WWa3gVYBSnAcRIxcpshtrDOkVe5CFzff0e9nk")
    assert "EAAj7W" in redacted          # enough to tell two tokens apart
    assert "shtrDOkVe5CFzff0e9nk" not in redacted  # but not enough to use


def test_short_token_is_fully_hidden():
    assert _redact("abc") == "***"


def test_the_codes_people_actually_hit_are_explained():
    for code in (190, 132000, 132001, 131047, 131030):
        assert code in _ERROR_HELP
        assert len(_ERROR_HELP[code]) > 40  # an explanation, not a label


def test_disabled_whatsapp_reports_instead_of_sending(caplog):
    config = WhatsAppConfig(
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
    assert send_test_message(config) is False
    assert "WHATSAPP_ENABLED" in caplog.text

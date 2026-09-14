"""Tests for the WhatsApp Cloud API client, using a fake HTTP session."""

from __future__ import annotations

import json

import pytest

from spotify_release_bot.whatsapp_client import WhatsAppClient, WhatsAppError


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"messages": [{"id": "wamid.1"}]}
        self.headers = {"Content-Type": "application/json"}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.payloads: list[dict] = []

    def post(self, url, json=None, **kwargs):
        self.payloads.append(json)
        if not self._responses:
            raise AssertionError("No fake response queued")
        return self._responses.pop(0)


def make_client(session, **kwargs):
    return WhatsAppClient(
        access_token="token",
        phone_number_id="123",
        recipient="31612345678",
        session=session,
        pause_between_messages=0,
        **kwargs,
    )


def test_text_message_payload_shape():
    session = FakeSession([FakeResponse()])
    make_client(session).send("Hello https://open.spotify.com/track/x")
    payload = session.payloads[0]
    assert payload["type"] == "text"
    assert payload["to"] == "31612345678"
    assert payload["text"]["preview_url"] is True


def test_template_message_payload_shape():
    session = FakeSession([FakeResponse()])
    client = make_client(session, template_name="new_release", template_language="nl")
    client.send("Artist - Song https://open.spotify.com/track/x")
    payload = session.payloads[0]
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "new_release"
    assert payload["template"]["language"]["code"] == "nl"
    parameters = payload["template"]["components"][0]["parameters"]
    assert parameters[0]["text"].startswith("Artist - Song")


def test_closed_24_hour_window_gives_an_actionable_error():
    session = FakeSession([FakeResponse(status_code=400, payload={"error": {"code": 131047}})])
    with pytest.raises(WhatsAppError, match="131047"):
        make_client(session).send("hi")


def test_send_all_respects_the_cap():
    session = FakeSession([FakeResponse(), FakeResponse()])
    sent = make_client(session).send_all(["a", "b", "c", "d"], max_messages=2)
    assert sent == 2
    assert len(session.payloads) == 2


def test_one_failed_message_does_not_stop_the_others():
    session = FakeSession(
        [
            FakeResponse(),
            FakeResponse(status_code=400, payload={"error": {"code": 131047}}),
            FakeResponse(),
        ]
    )
    sent = make_client(session).send_all(["a", "b", "c"], max_messages=10)
    assert sent == 2  # the middle one failed, the third still went out


def test_transient_server_error_is_retried(monkeypatch):
    monkeypatch.setattr("spotify_release_bot.whatsapp_client.time.sleep", lambda _: None)
    session = FakeSession([FakeResponse(status_code=503, payload={}), FakeResponse()])
    assert make_client(session).send("hi") == "wamid.1"

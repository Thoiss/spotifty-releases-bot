"""Sends messages through the official WhatsApp Cloud API (Meta Graph API).

Two platform limitations shape this module
------------------------------------------
1. **No groups.** The Cloud API exposes no endpoint for posting into a group
   chat, so this bot messages a single number directly. Nothing in this code
   can work around that; it is a Meta policy decision.

2. **The 24-hour window.** Free-form text may only be sent within 24 hours of
   the recipient's last message to your business number. Outside that window
   Meta rejects it with error 131047. A bot that fires at 00:03 will normally
   be outside the window, which is why this client can also send an *approved
   message template* instead - templates are allowed at any time.

Set ``template_name`` to use templates; leave it empty for plain text.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Sequence

import requests

_LOG = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.facebook.com"

# Meta's error code for "outside the 24 hour window, use a template".
_REENGAGEMENT_ERROR_CODE = 131047

_MAX_ATTEMPTS = 3


class WhatsAppError(RuntimeError):
    """A message could not be delivered to the Cloud API."""


class WhatsAppClient:
    """Small wrapper around the ``/{phone_number_id}/messages`` endpoint."""

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        recipient: str,
        api_version: str = "v21.0",
        template_name: str = "",
        template_language: str = "en",
        session: requests.Session | None = None,
        timeout: float = 20.0,
        pause_between_messages: float = 1.0,
    ) -> None:
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self._recipient = recipient
        self._api_version = api_version
        self._template_name = template_name
        self._template_language = template_language
        self._session = session or requests.Session()
        self._timeout = timeout
        self._pause = pause_between_messages

    @property
    def endpoint(self) -> str:
        return f"{GRAPH_BASE_URL}/{self._api_version}/{self._phone_number_id}/messages"

    @property
    def uses_template(self) -> bool:
        return bool(self._template_name)

    # ------------------------------------------------------------------
    # Public sending API
    # ------------------------------------------------------------------
    def send(self, body: str) -> str:
        """Send one message, as a template or as plain text.

        Which of the two is decided by whether a template name was configured,
        so the caller does not have to care.
        """
        if self.uses_template:
            return self.send_template(body)
        return self.send_text(body)

    def send_text(self, body: str) -> str:
        """Send a free-form text message (only valid inside the 24-hour window)."""
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._recipient,
            "type": "text",
            # preview_url makes WhatsApp render the Spotify link as a rich card.
            "text": {"preview_url": True, "body": body},
        }
        return self._post(payload)

    def send_template(self, parameter: str) -> str:
        """Send an approved template, filling its single {{1}} body variable.

        The template must already be approved in the WhatsApp Manager, and its
        body must contain exactly one variable, for example:
        "New release: {{1}}".
        """
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._recipient,
            "type": "template",
            "template": {
                "name": self._template_name,
                "language": {"code": self._template_language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": parameter}],
                    }
                ],
            },
        }
        return self._post(payload)

    def send_all(self, bodies: Sequence[str], max_messages: int) -> int:
        """Send several messages, pacing them and never exceeding ``max_messages``.

        One failed message does not abort the rest: a single bad link should not
        cost you the whole night's notifications.
        """
        to_send = list(bodies[:max_messages])
        skipped = len(bodies) - len(to_send)
        if skipped > 0:
            _LOG.warning(
                "Capping WhatsApp output at %d messages; %d not sent", max_messages, skipped
            )

        sent = 0
        for index, body in enumerate(to_send):
            try:
                self.send(body)
                sent += 1
            except WhatsAppError as exc:
                _LOG.error("Could not send WhatsApp message %d/%d: %s", index + 1, len(to_send), exc)
            if index < len(to_send) - 1 and self._pause > 0:
                time.sleep(self._pause)
        return sent

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _post(self, payload: dict[str, Any]) -> str:
        """POST one message payload, retrying only on transient failures."""
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        last_error = ""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self._session.post(
                    self.endpoint, json=payload, headers=headers, timeout=self._timeout
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                _LOG.warning("WhatsApp request failed (attempt %d): %s", attempt, exc)
                time.sleep(min(2 ** attempt, 10))
                continue

            if response.status_code < 300:
                data = response.json()
                message_id = (data.get("messages") or [{}])[0].get("id", "")
                _LOG.info("WhatsApp message accepted (id=%s)", message_id or "unknown")
                return message_id

            error = (response.json().get("error") if _is_json(response) else {}) or {}
            if error.get("code") == _REENGAGEMENT_ERROR_CODE:
                raise WhatsAppError(
                    "WhatsApp refused the message because the 24-hour customer service "
                    "window is closed (error 131047). Either send any message from your "
                    "own WhatsApp to the business number to reopen it, or configure "
                    "WHATSAPP_TEMPLATE_NAME with an approved template. See the README."
                )

            if response.status_code >= 500 or response.status_code == 429:
                last_error = f"{response.status_code} {response.text[:200]}"
                _LOG.warning("WhatsApp transient error (attempt %d): %s", attempt, last_error)
                time.sleep(min(2 ** attempt, 10))
                continue

            raise WhatsAppError(
                f"WhatsApp API rejected the message ({response.status_code}): "
                f"{response.text[:300]}"
            )

        raise WhatsAppError(
            f"WhatsApp message failed after {_MAX_ATTEMPTS} attempts: {last_error}"
        )


def _is_json(response: requests.Response) -> bool:
    return "application/json" in response.headers.get("Content-Type", "")

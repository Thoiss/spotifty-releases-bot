"""The ``whatsapp-test`` command: send one message and explain what happened.

Exists so the WhatsApp half can be debugged on its own. A full run costs
several hundred Spotify requests against a quota that is easily exhausted, and
spending that just to discover that a template name is wrong is a poor trade.
This sends exactly one message, touches Spotify not at all, and prints the
request body alongside Meta's reply.
"""

from __future__ import annotations

import json
import logging

from .config import WhatsAppConfig
from .whatsapp_client import WhatsAppClient, WhatsAppError

_LOG = logging.getLogger(__name__)

DEFAULT_MESSAGE = "Test Artist - Test Song https://open.spotify.com/track/2pbr8QVA1GsO6O1Oc7s2qK"

# Meta returns a numeric code with every rejection. The message text alone is
# often generic ("Invalid parameter"), so the code is what actually identifies
# the problem.
_ERROR_HELP: dict[int, str] = {
    0: "The access token is invalid, expired or was regenerated. Copy a fresh "
       "one into WHATSAPP_ACCESS_TOKEN, or create a permanent System User token.",
    3: "The token lacks the whatsapp_business_messaging permission. Regenerate "
       "it with both whatsapp_business_messaging and whatsapp_business_management.",
    10: "The app does not have permission for this WhatsApp account. Assign the "
        "WhatsApp account asset to the System User, not only the app.",
    100: "A parameter was rejected. Usually the template name or language does "
         "not match an approved template, or the number of {{n}} variables in "
         "the template differs from the one parameter this bot sends.",
    190: "The access token has expired. Temporary tokens last 24 hours; create "
         "a permanent System User token instead.",
    131_009: "A parameter value is not allowed - often the recipient number is "
             "malformed. Use international format, digits only, no '+'.",
    131_026: "The message could not be delivered: the recipient may not have "
             "WhatsApp, or the number is wrong.",
    131_030: "The recipient is not in your app's allowed list. While the app is "
             "in test mode, add the number under WhatsApp > API Setup > To.",
    131_047: "Outside the 24-hour window. This is exactly what templates are "
             "for - set WHATSAPP_TEMPLATE_NAME to an approved template.",
    131_051: "Unsupported message type for this recipient or number.",
    132_000: "The number of parameters does not match the template. This bot "
             "sends exactly one, so the template body needs exactly one {{1}}. "
             "The built-in 'hello_world' template has none, which is why it fails.",
    132_001: "No approved template with that name and language exists. Check "
             "WHATSAPP_TEMPLATE_NAME and WHATSAPP_TEMPLATE_LANGUAGE against "
             "WhatsApp Manager - the language code must match exactly.",
    132_007: "The template content was rejected by policy.",
    132_012: "A template parameter has the wrong format - no newlines, tabs, or "
             "runs of more than four spaces inside a variable.",
    133_010: "The phone number is not registered for the Cloud API.",
    368: "The account is temporarily blocked for policy violations.",
}


def _redact(token: str) -> str:
    if len(token) <= 12:
        return "***"
    return f"{token[:6]}...{token[-4:]} ({len(token)} chars)"


def send_test_message(config: WhatsAppConfig, message: str | None = None) -> bool:
    """Send one message. Returns True when Meta accepted it."""
    if not config.enabled:
        _LOG.error("WHATSAPP_ENABLED is false, so there is nothing to test.")
        _LOG.error("Set it to true in .env and try again.")
        return False

    body = message or DEFAULT_MESSAGE

    client = WhatsAppClient(
        access_token=config.access_token,
        phone_number_id=config.phone_number_id,
        recipient=config.recipient,
        api_version=config.api_version,
        template_name=config.template_name,
        template_language=config.template_language,
    )

    _LOG.info("Endpoint       : %s", client.endpoint)
    _LOG.info("Recipient      : %s", config.recipient)
    _LOG.info("Access token   : %s", _redact(config.access_token))
    if client.uses_template:
        _LOG.info(
            "Mode           : template %r (language %s)",
            config.template_name,
            config.template_language,
        )
        payload = client.build_template_payload(body)
    else:
        _LOG.info("Mode           : plain text (only valid inside the 24-hour window)")
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": config.recipient,
            "type": "text",
            "text": {"preview_url": True, "body": body},
        }

    _LOG.info("Request body   :\n%s", json.dumps(payload, indent=2, ensure_ascii=False))

    try:
        message_id = client.send(body)
    except WhatsAppError as exc:
        _LOG.error("FAILED: %s", exc)
        if exc.code is not None:
            explanation = _ERROR_HELP.get(int(exc.code))
            _LOG.error("Meta error code %s", exc.code)
            if explanation:
                _LOG.error("Meaning: %s", explanation)
            else:
                _LOG.error(
                    "That code is not in this tool's list; look it up in Meta's "
                    "cloud API error reference."
                )
        return False

    _LOG.info("SUCCESS - Meta accepted the message (id=%s)", message_id or "unknown")
    _LOG.info(
        "Accepted means queued, not delivered. If nothing arrives, check that the "
        "recipient number is on the app's allowed list while in test mode."
    )
    return True

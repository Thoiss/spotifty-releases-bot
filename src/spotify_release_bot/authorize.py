"""One-time helper that turns your Spotify login into a refresh token.

Why this exists
---------------
Reading your followed artists and writing to your playlist are things only
*you* may do, so the bot needs a token that represents you. Spotify hands those
out through the Authorization Code flow, which requires a browser exactly once.
You run this helper on your laptop, approve the permissions, and it prints a
refresh token. That token does not expire, so the container can use it forever
to mint short-lived access tokens on its own.

Usage:
    python -m spotify_release_bot authorize
"""

from __future__ import annotations

import base64
import secrets
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from .spotify_client import ACCOUNTS_TOKEN_URL, REQUIRED_SCOPES

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8080/callback"

_SUCCESS_PAGE = b"""<!doctype html><html><body style="font-family:sans-serif">
<h2>Authorised.</h2><p>You can close this tab and return to your terminal.</p>
</body></html>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches the single redirect Spotify sends back to us."""

    captured: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        for key in ("code", "state", "error"):
            if key in params:
                _CallbackHandler.captured[key] = params[key][0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_SUCCESS_PAGE)

    def log_message(self, *_args: object) -> None:
        """Silence the default per-request logging to stderr."""


def _exchange_code_for_tokens(
    code: str, client_id: str, client_secret: str, redirect_uri: str
) -> dict:
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    response = requests.post(
        ACCOUNTS_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Authorization": f"Basic {basic}"},
        timeout=30,
    )
    if response.status_code != 200:
        raise SystemExit(
            f"Spotify rejected the authorisation code ({response.status_code}): "
            f"{response.text[:300]}\n"
            "Check that the redirect URI below matches the one in your Spotify app "
            "settings exactly, character for character."
        )
    return response.json()


def _listen_for_callback(redirect_uri: str, timeout_seconds: float = 300.0) -> dict[str, str]:
    """Run a tiny web server until Spotify redirects back, or we give up."""
    parsed = urllib.parse.urlparse(redirect_uri)
    server = HTTPServer((parsed.hostname or "127.0.0.1", parsed.port or 80), _CallbackHandler)
    server.timeout = timeout_seconds
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    server.server_close()
    return dict(_CallbackHandler.captured)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    print("Spotify one-time authorisation")
    print("------------------------------")
    print(
        "Create an app at https://developer.spotify.com/dashboard, then add the "
        f"redirect URI below to it.\n"
    )

    client_id = input("Client ID: ").strip()
    client_secret = input("Client secret: ").strip()
    redirect_uri = (
        input(f"Redirect URI [{DEFAULT_REDIRECT_URI}]: ").strip() or DEFAULT_REDIRECT_URI
    )
    if not client_id or not client_secret:
        print("Client ID and client secret are both required.", file=sys.stderr)
        return 1

    # The state value protects against someone else's redirect being replayed
    # at us; we simply check that what comes back is what we sent.
    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(REQUIRED_SCOPES),
            "state": state,
            "show_dialog": "true",
        }
    )
    print("\nOpen this URL in your browser and approve the permissions:\n")
    print(f"{AUTHORIZE_URL}?{query}\n")

    captured: dict[str, str] = {}
    try:
        captured = _listen_for_callback(redirect_uri)
    except OSError as exc:
        print(f"(Could not open a local listener: {exc})")

    code = captured.get("code", "")
    if captured.get("error"):
        print(f"Spotify returned an error: {captured['error']}", file=sys.stderr)
        return 1
    if code and captured.get("state") != state:
        print("The state value did not match; aborting for safety.", file=sys.stderr)
        return 1

    if not code:
        # Fallback for when the browser runs on a different machine than this
        # script: the address bar still holds the code, so paste the whole URL.
        print("No callback received. Paste the full URL from your browser's address bar")
        print("(the one starting with the redirect URI) and press enter:")
        pasted = input("> ").strip()
        params = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
        code = (params.get("code") or [""])[0]
        if not code:
            print("That URL did not contain a ?code= parameter.", file=sys.stderr)
            return 1
        if (params.get("state") or [""])[0] != state:
            print("The state value did not match; aborting for safety.", file=sys.stderr)
            return 1

    tokens = _exchange_code_for_tokens(code, client_id, client_secret, redirect_uri)
    refresh_token = tokens.get("refresh_token", "")
    if not refresh_token:
        print("Spotify did not return a refresh token. Try again.", file=sys.stderr)
        return 1

    print("\nDone. Put these in your .env file:\n")
    print(f"SPOTIFY_CLIENT_ID={client_id}")
    print(f"SPOTIFY_CLIENT_SECRET={client_secret}")
    print(f"SPOTIFY_REFRESH_TOKEN={refresh_token}")
    print("\nKeep them secret: together they give full access to your Spotify account.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

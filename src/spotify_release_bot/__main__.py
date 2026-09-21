"""Command line entry point.

    python -m spotify_release_bot run        # stay running, check every night
    python -m spotify_release_bot once       # check now and exit
    python -m spotify_release_bot authorize  # one-time Spotify login helper

``run`` is what the Docker container uses; ``once`` is what you use to test.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace

from .app import run_once
from .config import AppConfig, ConfigError
from .logging_setup import configure_logging
from .scheduler import run_forever
from .spotify_client import SpotifyClient
from .state import StateStore
from .whatsapp_client import WhatsAppClient

_LOG = logging.getLogger("spotify_release_bot")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spotify_release_bot",
        description="Add new releases from followed Spotify artists to a playlist "
        "and announce them on WhatsApp.",
    )
    parser.add_argument(
        "command",
        choices=("run", "once", "authorize"),
        help="run: daily schedule; once: single check; authorize: get a refresh token",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would happen without touching the playlist, WhatsApp or the state file",
    )
    return parser


def _make_clients(config: AppConfig) -> tuple[SpotifyClient, WhatsAppClient | None, StateStore]:
    spotify = SpotifyClient(
        client_id=config.spotify.client_id,
        client_secret=config.spotify.client_secret,
        refresh_token=config.spotify.refresh_token,
        market=config.spotify.market,
        request_delay=config.spotify.request_delay,
    )
    whatsapp = None
    if config.whatsapp.enabled:
        whatsapp = WhatsAppClient(
            access_token=config.whatsapp.access_token,
            phone_number_id=config.whatsapp.phone_number_id,
            recipient=config.whatsapp.recipient,
            api_version=config.whatsapp.api_version,
            template_name=config.whatsapp.template_name,
            template_language=config.whatsapp.template_language,
        )
    state = StateStore(config.state_path)
    return spotify, whatsapp, state


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "authorize":
        from .authorize import main as authorize_main

        return authorize_main([])

    try:
        config = AppConfig.from_env()
    except ConfigError as exc:
        configure_logging("INFO")
        _LOG.error("Configuration problem: %s", exc)
        return 2

    if args.dry_run:
        # argparse wins over the environment variable, so a manual test run is
        # always safe even when DRY_RUN is unset in .env.
        config = replace(config, dry_run=True)

    configure_logging(config.log_level)
    spotify, whatsapp, state = _make_clients(config)

    if config.dry_run:
        _LOG.info("Dry run: nothing will be changed")

    def job() -> None:
        result = run_once(config, spotify, whatsapp, state)
        _LOG.info("Run finished: %s", result.summary())
        for error in result.errors:
            _LOG.warning("Run reported a problem: %s", error)

    if args.command == "once":
        try:
            job()
        except Exception:  # noqa: BLE001 - report cleanly instead of a raw traceback exit
            _LOG.exception("The run failed")
            return 1
        return 0

    _LOG.info(
        "Starting scheduler: daily at %s (%s)",
        config.run_at.strftime("%H:%M"),
        config.timezone.key,
    )
    try:
        run_forever(config.run_at, config.timezone, job)
    except KeyboardInterrupt:
        _LOG.info("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

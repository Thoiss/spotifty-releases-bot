"""One place that decides what log output looks like."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo


def configure_logging(level: str = "INFO", timezone: ZoneInfo | None = None) -> None:
    """Send readable, timestamped logs to stdout (where Docker collects them).

    Timestamps are rendered in ``timezone`` - the same one the schedule uses.
    A container's clock is UTC unless told otherwise, so without this a line
    reading "22:08" sits directly above "next check at 00:05, in 23h 56m",
    and the two look irreconcilable while both being right.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %Z",
    )
    if timezone is not None:
        formatter.converter = lambda timestamp: (
            datetime.fromtimestamp(timestamp, tz=timezone).timetuple()
        )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # urllib3 logs every connection at DEBUG, which drowns out our own output.
    logging.getLogger("urllib3").setLevel(max(numeric_level, logging.WARNING))

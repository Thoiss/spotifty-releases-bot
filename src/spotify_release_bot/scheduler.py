"""A minimal 'run every day at HH:MM' scheduler.

No extra dependency needed. The trick is to never sleep for a fixed 24 hours,
but to compute the next wall-clock occurrence each time. That way the schedule
stays correct across daylight saving time changes and across a container that
was paused or restarted.
"""

from __future__ import annotations

import logging
import time as time_module
from datetime import datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

_LOG = logging.getLogger(__name__)


def next_run_at(now: datetime, run_at: time, timezone: ZoneInfo) -> datetime:
    """The first moment at or after ``now`` that matches ``run_at`` locally.

    ``now`` is converted into the target timezone first, so the caller can pass
    a UTC timestamp without having to think about it.
    """
    local_now = now.astimezone(timezone)
    candidate = local_now.replace(
        hour=run_at.hour, minute=run_at.minute, second=0, microsecond=0
    )
    if candidate <= local_now:
        candidate = candidate + timedelta(days=1)
        # Re-apply the time after the date shift: adding a day across a DST
        # boundary can otherwise move the clock time by an hour.
        candidate = candidate.replace(hour=run_at.hour, minute=run_at.minute)
    return candidate


def run_forever(
    run_at: time,
    timezone: ZoneInfo,
    job: Callable[[], None],
    sleeper: Callable[[float], None] = time_module.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(tz=ZoneInfo("UTC")),
    max_iterations: int | None = None,
) -> None:
    """Block forever, calling ``job`` once per day at the configured time.

    ``sleeper``, ``clock`` and ``max_iterations`` exist so a test can drive this
    loop without actually waiting a day.
    """
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        target = next_run_at(clock(), run_at, timezone)
        wait_seconds = max((target - clock().astimezone(timezone)).total_seconds(), 0.0)
        _LOG.info(
            "Next check at %s (in %s)",
            target.strftime("%Y-%m-%d %H:%M %Z"),
            _human_duration(wait_seconds),
        )

        # Sleep in slices so a stopped/resumed container notices quickly that
        # the wall clock jumped, instead of oversleeping by the lost time.
        while wait_seconds > 0:
            sleeper(min(wait_seconds, 300.0))
            wait_seconds = (target - clock().astimezone(timezone)).total_seconds()

        try:
            job()
        except Exception:  # noqa: BLE001 - a nightly job must survive any failure
            _LOG.exception("The scheduled run failed; will try again tomorrow")

        iterations += 1


def _human_duration(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"

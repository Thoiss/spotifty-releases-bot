"""Tests for the 'next time it is 00:03' calculation."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from spotify_release_bot.scheduler import next_run_at

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
RUN_AT = time(0, 3)


def test_later_today():
    now = datetime(2026, 9, 14, 0, 0, tzinfo=AMSTERDAM)
    assert next_run_at(now, RUN_AT, AMSTERDAM) == datetime(2026, 9, 14, 0, 3, tzinfo=AMSTERDAM)


def test_rolls_over_to_tomorrow_once_the_time_has_passed():
    now = datetime(2026, 9, 14, 12, 0, tzinfo=AMSTERDAM)
    assert next_run_at(now, RUN_AT, AMSTERDAM) == datetime(2026, 9, 15, 0, 3, tzinfo=AMSTERDAM)


def test_exactly_on_the_minute_schedules_the_next_day():
    now = datetime(2026, 9, 14, 0, 3, tzinfo=AMSTERDAM)
    assert next_run_at(now, RUN_AT, AMSTERDAM) == datetime(2026, 9, 15, 0, 3, tzinfo=AMSTERDAM)


def test_a_utc_input_is_converted_to_local_time_first():
    # 23:00 UTC is already 01:00 in Amsterdam (summer time), so the next 00:03
    # is the following local day, not 'today'.
    now = datetime(2026, 9, 13, 23, 0, tzinfo=ZoneInfo("UTC"))
    result = next_run_at(now, RUN_AT, AMSTERDAM)
    assert (result.hour, result.minute) == (0, 3)
    assert result.date().isoformat() == "2026-09-15"


def test_clock_time_survives_the_dst_switch():
    # The night the clocks go back, the run must still be at 00:03 local.
    now = datetime(2026, 10, 24, 12, 0, tzinfo=AMSTERDAM)
    result = next_run_at(now, RUN_AT, AMSTERDAM)
    assert (result.hour, result.minute) == (0, 3)


def test_run_forever_waits_then_runs_the_job():
    """Drive the daemon loop with a fake clock so no real time passes."""
    from spotify_release_bot.scheduler import run_forever

    now = [datetime(2026, 9, 14, 0, 0, tzinfo=AMSTERDAM)]
    slept: list[float] = []
    runs: list[int] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] = now[0].fromtimestamp(now[0].timestamp() + seconds, tz=AMSTERDAM)

    run_forever(
        RUN_AT,
        AMSTERDAM,
        job=lambda: runs.append(1),
        sleeper=fake_sleep,
        clock=lambda: now[0],
        max_iterations=2,
    )

    assert runs == [1, 1]
    assert sum(slept) > 0


def test_run_forever_survives_a_failing_job():
    """A crash inside the nightly job must not kill the scheduler."""
    from spotify_release_bot.scheduler import run_forever

    now = [datetime(2026, 9, 14, 0, 0, tzinfo=AMSTERDAM)]

    def fake_sleep(seconds: float) -> None:
        now[0] = now[0].fromtimestamp(now[0].timestamp() + seconds, tz=AMSTERDAM)

    calls: list[int] = []

    def boom() -> None:
        calls.append(1)
        raise RuntimeError("Spotify is down")

    run_forever(
        RUN_AT, AMSTERDAM, job=boom, sleeper=fake_sleep, clock=lambda: now[0], max_iterations=2
    )
    assert calls == [1, 1]

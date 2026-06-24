"""Tests for calendar recurrence helpers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from mose.schedule import (
    RecurrenceError,
    compute_next_run,
    format_recurrence_human,
    validate_recurrence,
)


def test_validate_daily() -> None:
    rec = validate_recurrence({"frequency": "daily", "hour": 7, "minute": 30})
    assert rec["frequency"] == "daily"
    assert rec["hour"] == 7
    assert rec["minute"] == 30


def test_validate_weekly_requires_dow() -> None:
    with pytest.raises(RecurrenceError):
        validate_recurrence({"frequency": "weekly", "hour": 9, "minute": 0})


def test_compute_next_run_daily_before_time() -> None:
    tz = "America/Chicago"
    # 2026-06-24 10:00 Chicago = after 07:00 same day → next is tomorrow 07:00
    chicago = ZoneInfo(tz)
    after = datetime(2026, 6, 24, 10, 0, tzinfo=chicago).timestamp()
    nxt = compute_next_run(
        {"frequency": "daily", "hour": 7, "minute": 0},
        tz,
        after=after,
    )
    nxt_dt = datetime.fromtimestamp(nxt, tz=chicago)
    assert nxt_dt.date().isoformat() == "2026-06-25"
    assert nxt_dt.hour == 7
    assert nxt_dt.minute == 0


def test_compute_next_run_daily_same_day() -> None:
    tz = "UTC"
    after = datetime(2026, 6, 24, 5, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    nxt = compute_next_run(
        {"frequency": "daily", "hour": 7, "minute": 0},
        tz,
        after=after,
    )
    nxt_dt = datetime.fromtimestamp(nxt, tz=ZoneInfo("UTC"))
    assert nxt_dt.date().isoformat() == "2026-06-24"
    assert nxt_dt.hour == 7


def test_compute_next_run_weekly() -> None:
    tz = "UTC"
    # 2026-06-24 is Wednesday (weekday 2); next Monday is 2026-06-29
    after = datetime(2026, 6, 24, 12, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    nxt = compute_next_run(
        {"frequency": "weekly", "hour": 8, "minute": 0, "day_of_week": 0},
        tz,
        after=after,
    )
    nxt_dt = datetime.fromtimestamp(nxt, tz=ZoneInfo("UTC"))
    assert nxt_dt.weekday() == 0
    assert nxt_dt.hour == 8


def test_format_recurrence_human() -> None:
    text = format_recurrence_human(
        {"frequency": "daily", "hour": 7, "minute": 0},
        "America/Chicago",
    )
    assert "daily at 07:00" in text
    assert "America/Chicago" in text

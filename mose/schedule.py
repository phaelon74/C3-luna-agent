"""Calendar recurrence helpers for scheduled tasks (stdlib only)."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

VALID_FREQUENCIES = frozenset({"daily", "weekly", "monthly", "yearly"})


class RecurrenceError(ValueError):
    """Invalid recurrence specification."""


def validate_recurrence(recurrence: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate a recurrence dict. Raises RecurrenceError on invalid input."""
    if not isinstance(recurrence, dict):
        raise RecurrenceError("recurrence must be an object")
    freq = str(recurrence.get("frequency") or "").strip().lower()
    if freq not in VALID_FREQUENCIES:
        raise RecurrenceError(
            f"frequency must be one of: {', '.join(sorted(VALID_FREQUENCIES))}"
        )
    try:
        hour = int(recurrence.get("hour", 0))
        minute = int(recurrence.get("minute", 0))
    except (TypeError, ValueError) as e:
        raise RecurrenceError("hour and minute must be integers") from e
    if not (0 <= hour <= 23):
        raise RecurrenceError("hour must be 0-23")
    if not (0 <= minute <= 59):
        raise RecurrenceError("minute must be 0-59")

    out: dict[str, Any] = {"frequency": freq, "hour": hour, "minute": minute}

    if freq == "weekly":
        if "day_of_week" not in recurrence:
            raise RecurrenceError("weekly recurrence requires day_of_week (0=Mon … 6=Sun)")
        try:
            dow = int(recurrence["day_of_week"])
        except (TypeError, ValueError) as e:
            raise RecurrenceError("day_of_week must be an integer") from e
        if not (0 <= dow <= 6):
            raise RecurrenceError("day_of_week must be 0-6 (Monday=0)")
        out["day_of_week"] = dow

    if freq in ("monthly", "yearly"):
        if "day_of_month" not in recurrence:
            raise RecurrenceError(f"{freq} recurrence requires day_of_month (1-28)")
        try:
            dom = int(recurrence["day_of_month"])
        except (TypeError, ValueError) as e:
            raise RecurrenceError("day_of_month must be an integer") from e
        if not (1 <= dom <= 28):
            raise RecurrenceError("day_of_month must be 1-28")
        out["day_of_month"] = dom

    if freq == "yearly":
        if "month" not in recurrence:
            raise RecurrenceError("yearly recurrence requires month (1-12)")
        try:
            month = int(recurrence["month"])
        except (TypeError, ValueError) as e:
            raise RecurrenceError("month must be an integer") from e
        if not (1 <= month <= 12):
            raise RecurrenceError("month must be 1-12")
        out["month"] = month

    return out


def _at_time(d: date, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, 0, tzinfo=tz)


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def compute_next_run(
    recurrence: dict[str, Any],
    tz_name: str,
    *,
    after: float | None = None,
) -> float:
    """Return UTC epoch for the next scheduled run strictly after ``after`` (default: now)."""
    rec = validate_recurrence(recurrence)
    try:
        tz = ZoneInfo(tz_name)
    except Exception as e:
        raise RecurrenceError(f"invalid timezone: {tz_name}") from e

    after_ts = after if after is not None else time.time()
    after_dt = datetime.fromtimestamp(after_ts, tz=tz)
    hour = rec["hour"]
    minute = rec["minute"]
    freq = rec["frequency"]

    if freq == "daily":
        cand = _at_time(after_dt.date(), hour, minute, tz)
        if cand <= after_dt:
            cand = _at_time(after_dt.date() + timedelta(days=1), hour, minute, tz)
        return cand.timestamp()

    if freq == "weekly":
        target_dow = rec["day_of_week"]
        current = after_dt.date()
        days_ahead = (target_dow - current.weekday()) % 7
        cand_date = current + timedelta(days=days_ahead)
        cand = _at_time(cand_date, hour, minute, tz)
        if cand <= after_dt:
            cand = _at_time(cand_date + timedelta(days=7), hour, minute, tz)
        return cand.timestamp()

    if freq == "monthly":
        dom = rec["day_of_month"]
        year, month = after_dt.year, after_dt.month
        cand = _at_time(date(year, month, dom), hour, minute, tz)
        if cand <= after_dt:
            year, month = _next_month(year, month)
            cand = _at_time(date(year, month, dom), hour, minute, tz)
        return cand.timestamp()

    # yearly
    month = rec["month"]
    dom = rec["day_of_month"]
    year = after_dt.year
    cand = _at_time(date(year, month, dom), hour, minute, tz)
    if cand <= after_dt:
        cand = _at_time(date(year + 1, month, dom), hour, minute, tz)
    return cand.timestamp()


_DOW_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH_NAMES = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_recurrence_human(recurrence: dict[str, Any], tz_name: str) -> str:
    """Human-readable schedule for admin notifications."""
    rec = validate_recurrence(recurrence)
    hm = f"{rec['hour']:02d}:{rec['minute']:02d}"
    freq = rec["frequency"]
    if freq == "daily":
        detail = f"daily at {hm}"
    elif freq == "weekly":
        detail = f"weekly on {_DOW_NAMES[rec['day_of_week']]} at {hm}"
    elif freq == "monthly":
        detail = f"monthly on day {rec['day_of_month']} at {hm}"
    else:
        detail = (
            f"yearly on {_MONTH_NAMES[rec['month']]} {rec['day_of_month']} at {hm}"
        )
    return f"{detail} ({tz_name})"


def format_next_run(epoch: float, tz_name: str) -> str:
    """Format a next-run timestamp in the configured timezone."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.fromtimestamp(epoch, tz=tz).strftime("%Y-%m-%d %H:%M %Z")

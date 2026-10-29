"""Timestamp helpers. Internally all event times are float epoch seconds (UTC)."""
from __future__ import annotations

from datetime import datetime, timezone


def parse_ts(value) -> float:
    """Accept ISO-8601 strings (with Z or offset), epoch seconds or epoch millis."""
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    if not isinstance(value, str) or not value:
        raise ValueError(f"unparseable timestamp: {value!r}")
    s = value.strip()
    if s.replace(".", "", 1).isdigit():
        return parse_ts(float(s))
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # tolerate a space separator used by many legacy formats
    if len(s) > 10 and s[10] == " ":
        s = s[:10] + "T" + s[11:]
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def floor_to(ts: float, seconds: int) -> float:
    return ts - (ts % seconds)


def hour_of_week(ts: float) -> int:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.weekday() * 24 + dt.hour


def minute_of_week(ts: float) -> int:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return (dt.weekday() * 24 + dt.hour) * 60 + dt.minute

"""A tiny injectable clock so time-dependent services can be tested deterministically."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional, Protocol


class Clock(Protocol):
    def now(self, tz: Optional[tzinfo] = None) -> datetime: ...


class SystemClock:
    """`now()` is naive local time, matching the ISO strings the bot has always stored."""
    def now(self, tz: Optional[tzinfo] = None) -> datetime:
        return datetime.now(tz)


class FixedClock:
    """Settable clock for tests. A naive start value is interpreted as UTC when a timezone is requested."""
    def __init__(self, start: datetime):
        self._utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)

    def now(self, tz: Optional[tzinfo] = None) -> datetime:
        return self._utc.astimezone(tz) if tz else self._utc.replace(tzinfo=None)

    def advance(self, **kwargs) -> None:
        self._utc += timedelta(**kwargs)

    def set(self, value: datetime) -> None:
        self._utc = value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def utc_offset_zone(hours: int) -> timezone:
    return timezone(timedelta(hours=hours))

"""Per-account cooldowns (work, daily, crime, rob) stored as UTC 'YYYY-MM-DD HH:MM:SS' strings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Cooldown:
    field: str                 # account field holding the last-used timestamp
    duration: timedelta

    def remaining(self, account: dict, now_utc: datetime) -> Optional[timedelta]:
        """Time left, or None if the command may be used now. `now_utc` is naive UTC."""
        last = account.get(self.field)
        if not last:
            return None
        elapsed = now_utc - datetime.strptime(last, TIME_FORMAT)
        return self.duration - elapsed if elapsed < self.duration else None

    @staticmethod
    def stamp(now_utc: datetime) -> str:
        return now_utc.strftime(TIME_FORMAT)


def format_remaining(remaining: timedelta, *, hours: bool = False, always_minutes: bool = False) -> str:
    """'1h 5m 3s' style text. Minutes are dropped when zero unless hours are shown or `always_minutes` is set."""
    total = int(remaining.total_seconds())
    parts = []
    if hours:
        h, total = divmod(total, 3600)
        if h > 0:
            parts.append(f"{h}h")
        show_minutes = True if h > 0 else False
    else:
        h, show_minutes = 0, False
    m, s = divmod(total, 60)
    if m > 0 or show_minutes or always_minutes:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


WORK = Cooldown("last_work", timedelta(minutes=20))
DAILY = Cooldown("last_daily", timedelta(hours=24))
CRIME = Cooldown("last_crime", timedelta(minutes=30))
ROB = Cooldown("last_rob", timedelta(minutes=15))

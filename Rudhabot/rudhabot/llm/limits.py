"""Cost and abuse controls for hosted-LLM calls: per-user and per-guild token buckets, a daily request budget, and a
global concurrency cap. All in memory (they reset on restart, which only ever makes limits stricter, never looser)."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class TokenBucket:
    capacity: float
    refill_per_s: float
    tokens: float = -1.0
    stamp: float = 0.0

    def take(self, now: float) -> bool:
        if self.tokens < 0:
            self.tokens, self.stamp = self.capacity, now
        self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.refill_per_s)
        self.stamp = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    def retry_after(self) -> float:
        return max(0.0, (1 - self.tokens) / self.refill_per_s) if self.refill_per_s else float("inf")


@dataclass
class RateLimiter:
    per_user_per_hour: int = 12
    per_guild_per_hour: int = 120
    daily_budget: int = 800
    concurrency: int = 3
    now: Callable[[], float] = time.monotonic
    _users: dict = field(default_factory=dict)
    _guilds: dict = field(default_factory=dict)
    _day: Optional[int] = None
    _used_today: int = 0
    _sem: Optional[asyncio.Semaphore] = None

    def check(self, user_id: int, guild_id: Optional[int]) -> Optional[str]:
        """None if the request may proceed (and consumes quota), else a user-facing reason."""
        now = self.now()
        day = int(time.time() // 86400)
        if day != self._day:
            self._day, self._used_today = day, 0
        if self._used_today >= self.daily_budget:
            return "Rudhabot's AI budget for today is used up. The offline answer is shown instead."
        user = self._users.setdefault(user_id, TokenBucket(self.per_user_per_hour, self.per_user_per_hour / 3600))
        if not user.take(now):
            return f"You're asking quickly. Try again in about {int(user.retry_after()) + 1} s."
        if guild_id is not None:
            guild = self._guilds.setdefault(guild_id, TokenBucket(self.per_guild_per_hour, self.per_guild_per_hour / 3600))
            if not guild.take(now):
                user.tokens += 1
                return "This server is asking a lot right now. Try again in a minute."
        self._used_today += 1
        return None

    @property
    def semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.concurrency)
        return self._sem

"""The five threat levels and their standing orders (pre-authorised responses).

    1 Normal    garrison routine        obvious spam removed; alerts as usual
    2 Elevated  sentries doubled        + slowmode on surging channels, probation for new accounts that join,
                                          short timeouts for repeat spammers
    3 High      checkpoints up          + probation for every join, links from new members removed,
                                          staff asked to confirm pausing invites
    4 Severe    martial law             + staff asked to approve a lockdown of the affected channels,
                                          anti-nuke budgets halved
    5 Critical  fortress                + staff asked to approve a server-wide lockdown and pausing DMs/invites,
                                          anti-nuke budgets quartered

Detectors may raise the level up to `levels.auto_max` (default 4); Critical is staff-only by default. An automatically
raised level steps down one level after `levels.decay_minutes` without new significant signals. A level set by staff
holds until its hold expires (or staff change it). State is persisted, so a restart doesn't drop the guard.
Standing orders only *enable* responses; in shadow mode they are reported, not carried out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class Level(IntEnum):
    NORMAL = 1
    ELEVATED = 2
    HIGH = 3
    SEVERE = 4
    CRITICAL = 5

    @property
    def label(self) -> str:
        return LABELS[self][0]

    @property
    def theme(self) -> str:
        return LABELS[self][1]


LABELS = {Level.NORMAL: ("Normal", "garrison routine"), Level.ELEVATED: ("Elevated", "sentries doubled"),
          Level.HIGH: ("High", "checkpoints up"), Level.SEVERE: ("Severe", "martial law"),
          Level.CRITICAL: ("Critical", "fortress")}


@dataclass(frozen=True)
class StandingOrders:
    slowmode_seconds: int            # applied to a surging channel (0 = none)
    probation_new_accounts: bool     # new accounts (< joins.new_account_days) get probation on join
    probation_all_joins: bool
    remove_new_member_links: bool    # links from members who joined < 1 day ago are removed
    propose_pause_invites: bool
    propose_lockdown: str            # "", "affected", "server"
    propose_pause_dms: bool
    budget_factor: float             # anti-nuke budgets are multiplied by this
    timeout_repeat_spammers: bool


ORDERS = {
    Level.NORMAL: StandingOrders(0, False, False, False, False, "", False, 1.0, True),
    Level.ELEVATED: StandingOrders(5, True, False, False, False, "", False, 1.0, True),
    Level.HIGH: StandingOrders(15, True, True, True, True, "", False, 0.75, True),
    Level.SEVERE: StandingOrders(30, True, True, True, True, "affected", False, 0.5, True),
    Level.CRITICAL: StandingOrders(60, True, True, True, True, "server", True, 0.25, True),
}


def suggested_level(severity: float, families: int = 1, nuke: bool = False) -> Level:
    """The level an incident of this severity calls for."""
    if nuke and severity >= 0.8:
        return Level.SEVERE if families < 2 else Level.CRITICAL
    if severity >= 0.95 and families >= 2:
        return Level.CRITICAL
    if severity >= 0.8:
        return Level.SEVERE
    if severity >= 0.6:
        return Level.HIGH
    if severity >= 0.35:
        return Level.ELEVATED
    return Level.NORMAL


@dataclass
class ThreatState:
    level: Level = Level.NORMAL
    since: float = 0.0
    source: str = "default"          # auto | staff | default
    set_by: Optional[int] = None
    hold_until: Optional[float] = None
    last_signal: float = 0.0
    reason: str = ""
    history: list = field(default_factory=list)       # last 50 changes

    def to_dict(self) -> dict:
        return {"level": int(self.level), "since": self.since, "source": self.source, "set_by": self.set_by,
                "hold_until": self.hold_until, "last_signal": self.last_signal, "reason": self.reason,
                "history": self.history[-50:]}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ThreatState":
        if not d:
            return cls()
        return cls(Level(int(d.get("level", 1))), float(d.get("since", 0)), d.get("source", "default"), d.get("set_by"),
                   d.get("hold_until"), float(d.get("last_signal", 0)), d.get("reason", ""), list(d.get("history", [])))

    @property
    def orders(self) -> StandingOrders:
        return ORDERS[self.level]

    def _change(self, level: Level, now: float, source: str, reason: str, by: Optional[int]) -> tuple[Level, Level]:
        old = self.level
        self.level, self.since, self.source, self.reason, self.set_by = level, now, source, reason, by
        self.history.append({"ts": now, "from": int(old), "to": int(level), "source": source, "reason": reason[:200],
                             "by": by})
        self.history = self.history[-50:]
        return old, level

    def raise_to(self, level: Level, now: float, reason: str, auto_max: int) -> Optional[tuple[Level, Level]]:
        """Detectors asking for a level: only ever raises, and never above `auto_max`."""
        self.last_signal = max(self.last_signal, now)
        target = Level(min(int(level), auto_max))
        if target <= self.level:
            return None
        return self._change(target, now, "auto", reason, None)

    def set_by_staff(self, level: Level, now: float, by: int, reason: str, hold_s: float) -> tuple[Level, Level]:
        result = self._change(level, now, "staff", reason, by)
        self.hold_until = now + hold_s if hold_s > 0 else None
        self.last_signal = now
        return result

    def tick(self, now: float, decay_s: float) -> Optional[tuple[Level, Level]]:
        """Step down one level after `decay_s` quiet seconds (staff holds are respected)."""
        if self.level == Level.NORMAL:
            return None
        if self.hold_until is not None:
            if now < self.hold_until:
                return None
            self.hold_until = None
            self.last_signal = now - decay_s          # a staff level steps down as soon as its hold ends
        quiet_since = max(self.since, self.last_signal)
        if now - quiet_since < decay_s:
            return None
        return self._change(Level(self.level - 1), now, "auto", "quiet period: stepping down", None)

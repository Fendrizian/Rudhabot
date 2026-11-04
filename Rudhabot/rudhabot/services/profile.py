"""The server profile: the channels and roles Rudhabot uses, in one place.

The defaults (and RUDHABOT_*_ID environment variables) still work; `/setup` overrides any of them without a restart,
and every system reads the current value when it needs it (announcements, the audit log, loan notices, review
signals, the admin / banker / bankrupt roles, security's admin role). Kept in SQLite (or JSON with
RUDHABOT_STORAGE=json), with a short history of who changed what.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import timezone
from typing import Optional

from rudhabot.settings import Channels, Settings

CHANNEL_KEYS = {
    "notifications": "loan notices",
    "lottery": "lottery jackpots",
    "birthdays": "birthday greetings and cake deliveries",
    "audit": "the economy audit log",
    "reviews": "Clairvoyance review signals",
}
ROLE_KEYS = {
    "admin": "administrators (alongside the Administrator permission)",
    "banker": "bankers (read-only economy oversight, card holds, loan bans)",
    "bankrupt": "given to members whose loans default",
}
HISTORY = 20


@dataclass(frozen=True)
class ProfileEntry:
    key: str
    kind: str              # channel | role
    value: int
    default: int
    purpose: str

    @property
    def overridden(self) -> bool:
        return self.value != self.default


class ServerProfile:
    def __init__(self, store, settings: Settings, clock=None):
        self._store, self._settings, self._clock = store, settings, clock
        self._cache: Optional[dict] = None

    # ------------------------------------------------------------------ reading (live)
    def _data(self) -> dict:
        if self._cache is None:
            data = self._store.load() or {}
            data.setdefault("channels", {})
            data.setdefault("roles", {})
            data.setdefault("history", [])
            self._cache = data
        return self._cache

    def _default_role(self, key: str) -> int:
        s = self._settings
        return {"admin": s.admin_role_id, "banker": s.banker_role_id, "bankrupt": s.loans.bankrupt_role_id}[key]

    def channel(self, key: str) -> int:
        if key not in CHANNEL_KEYS:
            raise KeyError(key)
        return int(self._data()["channels"].get(key) or getattr(self._settings.channels, key))

    def role(self, key: str) -> int:
        if key not in ROLE_KEYS:
            raise KeyError(key)
        return int(self._data()["roles"].get(key) or self._default_role(key))

    @property
    def channels(self) -> Channels:
        """The settings' Channels with this server's overrides applied."""
        base = self._settings.channels
        return replace(base, **{f.name: self.channel(f.name) for f in fields(Channels) if f.name in CHANNEL_KEYS})

    def entries(self) -> list[ProfileEntry]:
        out = [ProfileEntry(k, "channel", self.channel(k), getattr(self._settings.channels, k), why)
               for k, why in CHANNEL_KEYS.items()]
        out += [ProfileEntry(k, "role", self.role(k), self._default_role(k), why) for k, why in ROLE_KEYS.items()]
        return out

    def history(self) -> list[dict]:
        return list(self._data()["history"])

    # ------------------------------------------------------------------ changing
    def set(self, key: str, value: Optional[int], by: int) -> tuple[int, int]:
        """Override `key` (or reset it to the default with value=None). Returns (old, new)."""
        kind = "channels" if key in CHANNEL_KEYS else "roles" if key in ROLE_KEYS else None
        if kind is None:
            raise KeyError(key)
        old = self.channel(key) if kind == "channels" else self.role(key)
        data = self._data()
        if value is None:
            data[kind].pop(key, None)
        else:
            data[kind][key] = int(value)
        new = self.channel(key) if kind == "channels" else self.role(key)
        when = self._clock.now(timezone.utc).isoformat(timespec="seconds") if self._clock else None
        data["history"] = (data["history"] + [{"key": key, "old": old, "new": new, "by": str(by), "at": when}])[-HISTORY:]
        self._store.save(data)
        return old, new

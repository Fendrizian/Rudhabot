"""Role incomes: members holding certain roles collect a set amount with /collect, each role on its own cooldown.

- Admins set them with /set-income (amount 0 removes one); everyone can see the list with /income.
- Each role has its own cooldown in hours (default 24, 1-720), counted from that member's last collection of that
  role. Missed periods don't pile up: collecting late just starts the next wait.
- A member with several income roles collects all the ready ones at once. Payouts go to the wallet through the
  user repository, so every one is in the audit trail (labelled /collect) and in the weekly report.
- The collection time is saved before the wallet is credited: a crash in between can lose one payout, never pay twice.

Kept in SQLite (`role_incomes`, `income_collections`), or JSON with RUDHABOT_STORAGE=json.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from rudhabot.domain.cooldown import TIME_FORMAT
from rudhabot.persistence.database import owned

DEFAULT_HOURS, MIN_HOURS, MAX_HOURS = 24, 1, 720
MAX_AMOUNT = 1_000_000
MAX_ROLES = 25
HISTORY = 50


class IncomeRefused(Exception):
    """A change that isn't allowed; the message is shown as-is."""


@dataclass(frozen=True)
class RoleIncome:
    role_id: int
    amount: int
    hours: int

    @property
    def every(self) -> timedelta:
        return timedelta(hours=self.hours)


@dataclass
class Collected:
    paid: list[RoleIncome] = field(default_factory=list)
    waiting: list[tuple[RoleIncome, timedelta]] = field(default_factory=list)
    total: int = 0
    wallet: int = 0

    @property
    def any_income(self) -> bool:
        return bool(self.paid or self.waiting)


class RoleIncomes:
    def __init__(self, config_store, collections_store, users, clock):
        self._config, self._users, self._clock = config_store, users, clock
        self._collected = owned(collections_store)         # member -> {role id: last collection (UTC stamp)}

    def _now(self) -> datetime:
        return self._clock.now(timezone.utc).replace(tzinfo=None)

    def _data(self) -> dict:
        data = self._config.load() or {}
        data.setdefault("roles", {})
        data.setdefault("history", [])
        return data

    # ------------------------------------------------------------------ reading
    def incomes(self) -> list[RoleIncome]:
        """Every role income, biggest first."""
        roles = self._data()["roles"]
        out = [RoleIncome(int(rid), int(v["amount"]), int(v.get("hours", DEFAULT_HOURS))) for rid, v in roles.items()]
        return sorted(out, key=lambda i: (-i.amount, i.role_id))

    def get(self, role_id: int) -> Optional[RoleIncome]:
        return next((i for i in self.incomes() if i.role_id == role_id), None)

    def history(self) -> list[dict]:
        return list(self._data()["history"])

    def ready_in(self, user_id, income: RoleIncome) -> Optional[timedelta]:
        """How long until this member can collect this income again, or None if they can now."""
        last = self._collected.value().get(str(user_id), {}).get(str(income.role_id))
        if not last:
            return None
        elapsed = self._now() - datetime.strptime(last, TIME_FORMAT)
        return income.every - elapsed if elapsed < income.every else None

    # ------------------------------------------------------------------ collecting
    def collect(self, user_id, role_ids: Iterable[int]) -> Collected:
        held = set(role_ids)
        result = Collected()
        uid = str(user_id)
        stamp = self._now().strftime(TIME_FORMAT)
        for income in self.incomes():
            if income.role_id not in held:
                continue
            remaining = self.ready_in(user_id, income)
            if remaining is not None:
                result.waiting.append((income, remaining))
            else:
                result.paid.append(income)
        if not result.paid:
            return result
        mine = self._collected.value().setdefault(uid, {})
        for income in result.paid:
            mine[str(income.role_id)] = stamp
        self._collected.persist(uid)                        # first the stamp, then the money (never pay twice)
        result.total = sum(i.amount for i in result.paid)
        account = self._users.get(user_id)
        result.wallet = int(account["wallet"]) + result.total
        self._users.update(user_id, wallet=result.wallet)
        return result

    # ------------------------------------------------------------------ admins
    def set(self, role_id: int, amount: int, hours: Optional[int], by) -> tuple[Optional[RoleIncome], Optional[RoleIncome]]:
        """Give `role_id` an income (amount 0 removes it). `hours` None keeps the current cooldown (24 h for a new
        income). Returns (old, new)."""
        if not 0 <= int(amount) <= MAX_AMOUNT:
            raise IncomeRefused(f"An income must be between 1 and {MAX_AMOUNT:,} (0 removes it).")
        if hours is not None and not MIN_HOURS <= int(hours) <= MAX_HOURS:
            raise IncomeRefused(f"The cooldown must be between {MIN_HOURS} and {MAX_HOURS} hours.")
        data = self._data()
        key = str(int(role_id))
        current = data["roles"].get(key)
        old = RoleIncome(int(role_id), int(current["amount"]), int(current.get("hours", DEFAULT_HOURS))) if current else None
        if amount == 0:
            if old is None:
                raise IncomeRefused("That role has no income to remove.")
            del data["roles"][key]
            new = None
        else:
            if old is None and len(data["roles"]) >= MAX_ROLES:
                raise IncomeRefused(f"At most {MAX_ROLES} roles can have an income.")
            keep = old.hours if old is not None else DEFAULT_HOURS
            new = RoleIncome(int(role_id), int(amount), int(hours) if hours is not None else keep)
            data["roles"][key] = {"amount": new.amount, "hours": new.hours, "by": str(by),
                                  "updated": self._now().strftime(TIME_FORMAT)}
        self._log(data, role_id, old, new, by)
        self._config.save(data)
        return old, new

    def prune(self, existing_role_ids: Iterable[int], by="system") -> list[RoleIncome]:
        """Drop incomes whose role no longer exists in the server. Returns what was removed."""
        existing = set(existing_role_ids)
        data = self._data()
        gone = [i for i in self.incomes() if i.role_id not in existing]
        for income in gone:
            del data["roles"][str(income.role_id)]
            self._log(data, income.role_id, income, None, by, note="role deleted")
        if gone:
            self._config.save(data)
        return gone

    def _log(self, data: dict, role_id: int, old: Optional[RoleIncome], new: Optional[RoleIncome], by,
             note: str = "") -> None:
        entry = {"role": str(role_id), "old": [old.amount, old.hours] if old else None,
                 "new": [new.amount, new.hours] if new else None, "by": str(by),
                 "at": self._now().strftime(TIME_FORMAT)}
        if note:
            entry["note"] = note
        data["history"] = (data["history"] + [entry])[-HISTORY:]

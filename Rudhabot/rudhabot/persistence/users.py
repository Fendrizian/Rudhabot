"""Account persistence (data.json).

Accounts stay plain dicts so existing data files and command code keep working, but every mutation goes
through this class instead of module-level load/save helpers.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Mapping, Optional

from rudhabot.persistence.json_store import JsonStore

#: Fields `update()` may set. Anything else is a programming error.
ACCOUNT_FIELDS = frozenset({
    "wallet", "bank", "inventory", "last_daily", "last_work", "last_crime", "last_rob",
    "loan", "loan_ban_until", "loan_warning_sent",
})


def money_snapshot(account: Optional[Mapping]) -> dict[str, float]:
    """Every money-like balance on an account: wallet, bank and crypto holdings."""
    if not account:
        return {}
    out: dict[str, float] = {"wallet": int(account.get("wallet", 0) or 0), "bank": int(account.get("bank", 0) or 0)}
    for coin, amount in (account.get("crypto") or {}).items():
        out[f"crypto:{coin}"] = float(amount or 0)
    return out


class UserRepository:
    """Every write reports its money changes to `audit` (an AuditService), whichever command or job made it."""

    def __init__(self, store: JsonStore, coin_symbols: Iterable[str], audit=None):
        self._store = store
        self._coin_symbols = tuple(coin_symbols)
        self.audit = audit

    def _report(self, uid: str, before: Mapping[str, float], after: Mapping[str, float]) -> None:
        if self.audit is None:
            return
        for key in sorted(set(before) | set(after)):
            delta = after.get(key, 0) - before.get(key, 0)
            if abs(delta) > 1e-9:
                self.audit.record(uid, key, delta, after.get(key, 0))

    # ------------------------------------------------------------------ reads
    def get(self, user_id: int | str) -> dict:
        """Return the account, creating or repairing it if needed. The dict is a snapshot: persist changes
        with `update()`."""
        uid = str(user_id)
        data = self._store.load()
        account = data.get(uid)
        dirty = account is None
        if dirty:
            account = self._blank_account()
        else:
            dirty = self._repair(account)
        wallet, bank = int(account.get("wallet", 0)), int(account.get("bank", 0))
        if account.get("wallet") != wallet or account.get("bank") != bank:
            dirty = True
        account["wallet"], account["bank"] = wallet, bank
        if dirty:
            data[uid] = account
            self._store.save(data)
        return account

    def all(self) -> dict[str, dict]:
        """Every raw account keyed by user-id string (a snapshot; use `transaction()` to mutate)."""
        return self._store.load()

    # ----------------------------------------------------------------- writes
    def update(self, user_id: int | str, **fields: Any) -> None:
        """Set only the fields you pass. Passing `loan=None` clears the loan; *omitting* `loan` leaves it alone.

        (The legacy `update_user_data` silently reset `loan` to None on every call that did not pass it, which
        cancelled the debt of anyone who ran any other command.)
        """
        unknown = set(fields) - ACCOUNT_FIELDS
        if unknown:
            raise TypeError(f"unknown account field(s): {', '.join(sorted(unknown))}")
        uid = str(user_id)
        data = self._store.load()
        account = data.get(uid)
        before = money_snapshot(account)
        if account is None:
            account = self._blank_account()
        else:
            self._repair(account)
        for name, value in fields.items():
            account[name] = int(value) if name in ("wallet", "bank") else value
        data[uid] = account
        self._store.save(data)
        self._report(uid, before, money_snapshot(account))

    def save_all(self, data: Mapping[str, dict]) -> None:
        before = {uid: money_snapshot(acc) for uid, acc in self._store.load().items()}
        self._store.save(dict(data))
        self._report_all(before, data)

    @contextmanager
    def transaction(self) -> Iterator[dict[str, dict]]:
        """Load every account, let the caller mutate them in memory, and save only if the block succeeds."""
        data = self._store.load()
        before = {uid: money_snapshot(acc) for uid, acc in data.items()}
        yield data
        self._store.save(data)
        self._report_all(before, data)

    def _report_all(self, before: Mapping[str, Mapping], data: Mapping[str, dict]) -> None:
        for uid in sorted(set(before) | set(data)):
            self._report(uid, before.get(uid, {}), money_snapshot(data.get(uid)))

    # -------------------------------------------------- convenience (atomic)
    def credit_wallet(self, user_id: int | str, amount: int) -> int:
        """Add `amount` (may be negative) to the wallet and return the new balance."""
        account = self.get(user_id)
        new_balance = int(account["wallet"]) + int(amount)
        self.update(user_id, wallet=new_balance)
        return new_balance

    def consume_item(self, user_id: int | str, item: str, quantity: int = 1) -> bool:
        """Remove `quantity` of `item` from the inventory. Returns False (changing nothing) if not enough."""
        if quantity < 1:
            return False
        inventory = self.get(user_id).get("inventory", {})
        if inventory.get(item, 0) < quantity:
            return False
        inventory[item] -= quantity
        if inventory[item] <= 0:
            del inventory[item]
        self.update(user_id, inventory=inventory)
        return True

    # ----------------------------------------------- monthly special purchases
    def has_purchased_special(self, user_id: int | str, item: str, month: int) -> bool:
        account = self._store.load().get(str(user_id))
        if account is None:
            return False
        return item in account.get("monthly_purchases", {}).get(str(month), [])

    def record_special_purchase(self, user_id: int | str, item: str, month: int) -> None:
        uid, key = str(user_id), str(month)
        data = self._store.load()
        account = data.setdefault(uid, {"wallet": 0, "bank": 0, "inventory": {}, "monthly_purchases": {}})
        account.setdefault("monthly_purchases", {}).setdefault(key, []).append(item)
        self._store.save(data)

    # --------------------------------------------------------------- internals
    def _blank_account(self) -> dict:
        return {
            "wallet": 0,
            "bank": 0,
            "inventory": {},
            "loan": None,
            "loan_ban_until": None,
            "loan_warning_sent": False,
            "monthly_purchases": {},
            "crypto": {coin: 0.0 for coin in self._coin_symbols},
        }

    def _repair(self, account: dict) -> bool:
        """Fill in any missing standard fields on an existing account. Returns True if anything changed."""
        defaults = self._blank_account()
        changed = False
        for key in ("inventory", "loan", "loan_ban_until", "loan_warning_sent", "crypto", "monthly_purchases"):
            if key not in account:
                account[key] = defaults[key]
                changed = True
        return changed

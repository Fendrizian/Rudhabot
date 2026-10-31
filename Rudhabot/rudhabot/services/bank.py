"""The bank: card tiers with monthly limits, savings accounts with interest, and credit cards.

Wallet and bank balances still live in `UserRepository` (so their changes are audited automatically); savings and
credit-card balances live in `bank.json` and every change to them is reported to the audit trail here.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

from rudhabot.clock import Clock
from rudhabot.domain.bank import (SAVINGS_WITHDRAW_COOLDOWN_H, TIER_BY_KEY, TIERS, Tier, accrue, month_key, next_tier,
                                  roll_statement)
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.users import UserRepository


class BankRefused(Exception):
    """A user-facing refusal; the message is shown as-is."""


def blank_profile(today_iso: str) -> dict:
    return {"tier": "red", "savings": 0, "interest_carry": 0.0, "interest_date": today_iso, "savings_withdrawn_at": None,
            "usage": {"month": None, "deposit": 0, "withdraw": 0},
            "credit": {"balance": 0, "statement": 0, "period": None, "due": None, "frozen": False, "late_charged": False}}


class BankService:
    def __init__(self, store: JsonStore, users: UserRepository, clock: Clock, audit=None):
        self._store, self._users, self._clock, self._audit = store, users, clock, audit

    # ------------------------------------------------------------------ profile plumbing
    def _today(self):
        return self._clock.now().date()

    def _load(self, uid: str) -> tuple[dict, dict]:
        data = self._store.load()
        profile = data.get(uid)
        if profile is None:
            profile = blank_profile(self._today().isoformat())
        else:
            base = blank_profile(self._today().isoformat())
            base.update(profile)
            base["credit"] = {**blank_profile("")["credit"], **profile.get("credit", {})}
            profile = base
        return data, profile

    def _save(self, data: dict, uid: str, profile: dict) -> None:
        data[uid] = profile
        self._store.save(data)

    def _record(self, uid: str, account: str, delta, after, reason: Optional[str] = None) -> None:
        if self._audit is not None:
            self._audit.record(uid, account, delta, after, reason=reason)

    def _refresh(self, uid: str, profile: dict) -> None:
        """Apply interest due and roll the credit statement (lazy: done whenever the account is touched)."""
        today = self._today()
        last = datetime.fromisoformat(profile["interest_date"]).date() if profile.get("interest_date") else today
        days = (today - last).days
        if days > 0:
            interest, carry = accrue(profile["savings"], profile["interest_carry"], self.tier_of(profile), days)
            profile["interest_carry"], profile["interest_date"] = carry, today.isoformat()
            if interest:
                profile["savings"] += interest
                self._record(uid, "savings", interest, profile["savings"], reason=f"savings interest ({days} day(s))")
        late = roll_statement(profile["credit"], today)
        if late:
            self._record(uid, "credit", late, profile["credit"]["balance"], reason="credit card late interest")
        usage = profile["usage"]
        if usage.get("month") != month_key(today):
            profile["usage"] = {"month": month_key(today), "deposit": 0, "withdraw": 0}

    def account(self, user_id) -> dict:
        uid = str(user_id)
        data, profile = self._load(uid)
        self._refresh(uid, profile)
        self._save(data, uid, profile)
        return profile

    @staticmethod
    def tier_of(profile: dict) -> Tier:
        return TIER_BY_KEY.get(profile.get("tier"), TIERS[0])

    def net_worth(self, user_id) -> int:
        acc = self._users.get(user_id)
        return int(acc["wallet"]) + int(acc["bank"]) + int(self.account(user_id)["savings"])

    # ------------------------------------------------------------------ deposits and withdrawals (monthly limits)
    def remaining(self, profile: dict, kind: str) -> int:
        return max(0, self.tier_of(profile).monthly_limit - profile["usage"][kind])

    def transfer(self, user_id, amount: Optional[int], kind: str) -> int:
        """kind = "deposit" (wallet -> bank) or "withdraw" (bank -> wallet). amount=None means "all allowed"."""
        uid = str(user_id)
        data, profile = self._load(uid)
        self._refresh(uid, profile)
        source, destination = ("wallet", "bank") if kind == "deposit" else ("bank", "wallet")
        acc = self._users.get(uid)
        left = self.remaining(profile, kind)
        if amount is None:
            amount = min(acc[source], left)
        if amount <= 0:
            if left <= 0:
                raise BankRefused(self._limit_message(profile, kind))
            raise BankRefused("❌ Amount must be positive." if acc[source] > 0 else f"❌ Your {source} is empty.")
        if amount > acc[source]:
            raise BankRefused(f"❌ You don't have enough money in your {source}.")
        if amount > left:
            raise BankRefused(self._limit_message(profile, kind))
        self._users.update(uid, **{source: acc[source] - amount, destination: acc[destination] + amount})
        profile["usage"][kind] += amount
        self._save(data, uid, profile)
        return amount

    def _limit_message(self, profile: dict, kind: str) -> str:
        tier = self.tier_of(profile)
        return (f"❌ Your {tier.emoji} **{tier.name}** card allows {tier.monthly_limit:,} in {kind}s per month; "
                f"{self.remaining(profile, kind):,} left this month. Upgrade with `/bank upgrade`.")

    # ------------------------------------------------------------------ savings
    def savings_deposit(self, user_id, amount: Optional[int]) -> int:
        """Move money from the bank balance into savings (not limited by the monthly card limit)."""
        uid = str(user_id)
        data, profile = self._load(uid)
        self._refresh(uid, profile)
        acc = self._users.get(uid)
        amount = acc["bank"] if amount is None else amount
        if amount <= 0 or amount > acc["bank"]:
            raise BankRefused("❌ Move a positive amount that you have in your bank balance.")
        self._users.update(uid, bank=acc["bank"] - amount)
        profile["savings"] += amount
        self._record(uid, "savings", amount, profile["savings"])
        self._save(data, uid, profile)
        return amount

    def savings_withdraw(self, user_id, amount: Optional[int]) -> int:
        uid = str(user_id)
        data, profile = self._load(uid)
        self._refresh(uid, profile)
        now = self._clock.now()
        last = profile.get("savings_withdrawn_at")
        if last:
            ready = datetime.fromisoformat(last) + timedelta(hours=SAVINGS_WITHDRAW_COOLDOWN_H)
            if now < ready:
                hours = math.ceil((ready - now).total_seconds() / 3600)
                raise BankRefused(f"❌ Savings allow one withdrawal per day. Try again in about {hours} h.")
        amount = profile["savings"] if amount is None else amount
        if amount <= 0 or amount > profile["savings"]:
            raise BankRefused("❌ Withdraw a positive amount that you have in savings.")
        profile["savings"] -= amount
        profile["savings_withdrawn_at"] = now.isoformat(timespec="seconds")
        self._record(uid, "savings", -amount, profile["savings"])
        acc = self._users.get(uid)
        self._users.update(uid, bank=acc["bank"] + amount)
        self._save(data, uid, profile)
        return amount

    # ------------------------------------------------------------------ tiers
    def upgrade(self, user_id) -> Tier:
        uid = str(user_id)
        data, profile = self._load(uid)
        self._refresh(uid, profile)
        target = next_tier(profile["tier"])
        if target is None:
            raise BankRefused("💳 You already hold the highest card tier.")
        acc = self._users.get(uid)
        worth = acc["wallet"] + acc["bank"] + profile["savings"]
        if worth < target.net_worth_required:
            raise BankRefused(f"❌ {target.emoji} **{target.name}** needs a net worth of {target.net_worth_required:,} "
                              f"(wallet + bank + savings). You have {worth:,}.")
        if acc["bank"] < target.upgrade_fee:
            raise BankRefused(f"❌ The {target.name} upgrade fee is {target.upgrade_fee:,}, paid from your bank balance.")
        self._users.update(uid, bank=acc["bank"] - target.upgrade_fee)
        profile["tier"] = target.key
        self._save(data, uid, profile)
        return target

    # ------------------------------------------------------------------ credit card
    def credit_available(self, profile: dict) -> int:
        return max(0, self.tier_of(profile).credit_limit - profile["credit"]["balance"])

    def charge(self, user_id, amount: int, reason: str) -> int:
        """Put a purchase on the card. Raises BankRefused if the card is frozen or over its limit."""
        uid = str(user_id)
        data, profile = self._load(uid)
        self._refresh(uid, profile)
        credit = profile["credit"]
        if credit.get("frozen"):
            raise BankRefused("❌ Your card is frozen until this month's statement is paid (`/card pay`).")
        if amount > self.credit_available(profile):
            raise BankRefused(f"❌ Card declined: {self.credit_available(profile):,} of credit available.")
        credit["balance"] += amount
        self._record(uid, "credit", amount, credit["balance"], reason=f"card purchase: {reason}")
        self._save(data, uid, profile)
        return credit["balance"]

    def pay_card(self, user_id, amount: Optional[int], source: str = "wallet") -> int:
        uid = str(user_id)
        data, profile = self._load(uid)
        self._refresh(uid, profile)
        credit = profile["credit"]
        acc = self._users.get(uid)
        amount = min(credit["balance"], acc[source]) if amount is None else min(amount, credit["balance"])
        if credit["balance"] <= 0:
            raise BankRefused("✅ Your card has nothing to pay.")
        if amount <= 0 or amount > acc[source]:
            raise BankRefused(f"❌ You don't have that much in your {source}.")
        self._users.update(uid, **{source: acc[source] - amount})
        credit["balance"] -= amount
        credit["statement"] = max(0, credit["statement"] - amount)
        if credit["statement"] == 0:
            credit["frozen"] = False
        self._record(uid, "credit", -amount, credit["balance"], reason=f"card payment from {source}")
        self._save(data, uid, profile)
        return amount

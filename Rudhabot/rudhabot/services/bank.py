"""The bank: card tiers with monthly limits, daily interest on the bank balance, and credit cards.

Wallet and bank balances live in `UserRepository`, so their changes (interest included) are audited automatically.
Tier, interest bookkeeping and credit cards live in `bank.json`; credit-card changes are reported to the audit trail here.

Interest is paid by `apply_daily_interest()`, which a background task runs every hour; each account is credited at most
once per day, on its balance at that moment. (Paying lazily on access would let someone deposit a lot and then
collect several days' interest on it.) Separate savings accounts no longer exist: any old savings are moved into the
bank balance the first time the account is touched.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from rudhabot.clock import Clock
from rudhabot.domain.bank import TIER_BY_KEY, TIERS, Tier, accrue, month_key, next_tier, roll_statement
from rudhabot.persistence.json_store import JsonStore, OwnedJson, json_copy
from rudhabot.persistence.users import UserRepository
from rudhabot.services.audit import audit_context

MAX_CATCH_UP_DAYS = 7          # after downtime, pay at most a week of missed interest


class BankRefused(Exception):
    """A user-facing refusal; the message is shown as-is."""


def blank_profile(today_iso: str) -> dict:
    return {"tier": "red", "interest_carry": 0.0, "interest_date": today_iso,
            "usage": {"month": None, "deposit": 0, "withdraw": 0},
            "credit": {"balance": 0, "statement": 0, "period": None, "due": None, "frozen": False, "late_charged": False}}


class BankService:
    def __init__(self, store: JsonStore, users: UserRepository, clock: Clock, audit=None):
        self._store, self._users, self._clock, self._audit = store, users, clock, audit
        self._data = OwnedJson(store)

    # ------------------------------------------------------------------ profile plumbing
    def _today(self):
        return self._clock.now().date()

    def _normalised(self, stored: Optional[dict]) -> dict:
        """A complete profile in the current layout (missing fields filled in), as a new object."""
        profile = blank_profile(self._today().isoformat())
        if stored is not None:
            profile.update(json_copy(stored))
            profile["credit"] = {**blank_profile("")["credit"], **json_copy(stored.get("credit", {}))}
        return profile

    def _profile(self, uid: str) -> dict:
        """The member's profile, refreshed (statement roll, monthly limits, legacy savings).

        A stored profile that refreshing changed is saved *immediately*, so an operation that is then refused can
        neither lose what refreshing did nor repeat it next time (late interest charged and audited once; old savings
        moved into the bank exactly once). A member with no stored profile gets a blank one that isn't written until
        an operation commits it (looking someone up doesn't create data)."""
        data = self._data.value()
        stored = data.get(uid)
        profile = self._normalised(stored)
        self._refresh(uid, profile)
        if stored is not None and profile != stored:
            data[uid] = json_copy(profile)
            self._data.persist()
        return profile

    def _commit(self, uid: str, profile: dict) -> None:
        self._data.value()[uid] = json_copy(profile)
        self._data.persist()

    def _record(self, uid: str, account: str, delta, after, reason: Optional[str] = None) -> None:
        if self._audit is not None:
            self._audit.record(uid, account, delta, after, reason=reason)

    def _refresh(self, uid: str, profile: dict) -> None:
        """Roll the credit statement and the monthly limits, and fold any old savings into the bank balance."""
        today = self._today()
        legacy = int(profile.pop("savings", 0) or 0)
        profile.pop("savings_withdrawn_at", None)
        if legacy > 0:                                  # savings accounts were merged into the bank balance
            acc = self._users.get(uid)
            self._record(uid, "savings", -legacy, 0, reason="savings merged into bank")
            with audit_context("savings merged into bank"):
                self._users.update(uid, bank=acc["bank"] + legacy)
        late = roll_statement(profile["credit"], today)
        if late:
            self._record(uid, "credit", late, profile["credit"]["balance"], reason="credit card late interest")
        usage = profile["usage"]
        if usage.get("month") != month_key(today):
            profile["usage"] = {"month": month_key(today), "deposit": 0, "withdraw": 0}

    def apply_daily_interest(self) -> int:
        """Credit each bank balance once a day (catching up at most MAX_CATCH_UP_DAYS). Returns accounts credited.

        Crash-safe: every account's new interest date is saved *before* any interest is paid, so a crash part-way
        can at worst skip one day for some accounts; it can never pay the same day twice."""
        today = self._today()
        data = self._data.value()
        payouts = []
        for uid, account in self._users.all().items():
            stored = data.get(uid)
            if stored is None:
                if int(account.get("bank", 0) or 0) > 0:
                    data[uid] = blank_profile(today.isoformat())     # starts earning from today
                continue
            profile = self._normalised(stored)
            self._refresh(uid, profile)
            last = datetime.fromisoformat(profile["interest_date"]).date() if profile.get("interest_date") else today
            days = min((today - last).days, MAX_CATCH_UP_DAYS)
            if days > 0:
                balance = int(self._users.get(uid)["bank"])
                interest, carry = accrue(balance, profile.get("interest_carry", 0.0), self.tier_of(profile), days)
                profile["interest_carry"], profile["interest_date"] = carry, today.isoformat()
                if interest:
                    payouts.append((uid, interest, days))
            data[uid] = profile
        self._data.persist()                            # 1. remember that today is paid ...
        for uid, interest, days in payouts:             # 2. ... then pay
            with audit_context(f"bank interest ({days} day(s))"):
                self._users.update(uid, bank=int(self._users.get(uid)["bank"]) + interest)
        return len(payouts)

    def account(self, user_id) -> dict:
        """The member's bank profile (a copy: changing it changes nothing)."""
        return self._profile(str(user_id))

    @staticmethod
    def tier_of(profile: dict) -> Tier:
        return TIER_BY_KEY.get(profile.get("tier"), TIERS[0])

    def net_worth(self, user_id) -> int:
        self.account(user_id)                          # folds any old savings into the bank first
        acc = self._users.get(user_id)
        return int(acc["wallet"]) + int(acc["bank"])

    # ------------------------------------------------------------------ deposits and withdrawals (monthly limits)
    def remaining(self, profile: dict, kind: str) -> int:
        return max(0, self.tier_of(profile).monthly_limit - profile["usage"][kind])

    def transfer(self, user_id, amount: Optional[int], kind: str) -> int:
        """kind = "deposit" (wallet -> bank) or "withdraw" (bank -> wallet). amount=None means "all allowed"."""
        uid = str(user_id)
        profile = self._profile(uid)
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
        self._commit(uid, profile)
        return amount

    def _limit_message(self, profile: dict, kind: str) -> str:
        tier = self.tier_of(profile)
        return (f"❌ Your {tier.emoji} **{tier.name}** card allows {tier.monthly_limit:,} in {kind}s per month; "
                f"{self.remaining(profile, kind):,} left this month. Upgrade with `/bank upgrade`.")

    # ------------------------------------------------------------------ tiers
    def upgrade(self, user_id) -> Tier:
        uid = str(user_id)
        profile = self._profile(uid)
        target = next_tier(profile["tier"])
        if target is None:
            raise BankRefused("💳 You already hold the highest card tier.")
        acc = self._users.get(uid)
        worth = acc["wallet"] + acc["bank"]
        if worth < target.net_worth_required:
            raise BankRefused(f"❌ {target.emoji} **{target.name}** needs a net worth of {target.net_worth_required:,} "
                              f"(wallet + bank). You have {worth:,}.")
        if acc["bank"] < target.upgrade_fee:
            raise BankRefused(f"❌ The {target.name} upgrade fee is {target.upgrade_fee:,}, paid from your bank balance.")
        self._users.update(uid, bank=acc["bank"] - target.upgrade_fee)
        profile["tier"] = target.key
        self._commit(uid, profile)
        return target

    # ------------------------------------------------------------------ credit card
    def credit_available(self, profile: dict) -> int:
        return max(0, self.tier_of(profile).credit_limit - profile["credit"]["balance"])

    def charge(self, user_id, amount: int, reason: str) -> int:
        """Put a purchase on the card. Raises BankRefused if the card is frozen or over its limit."""
        uid = str(user_id)
        profile = self._profile(uid)
        credit = profile["credit"]
        if credit.get("hold"):
            raise BankRefused(f"❌ Your card is on hold by the bank: {credit['hold']['reason']}")
        if credit.get("frozen"):
            raise BankRefused("❌ Your card is frozen until this month's statement is paid (`/card pay`).")
        if amount > self.credit_available(profile):
            raise BankRefused(f"❌ Card declined: {self.credit_available(profile):,} of credit available.")
        credit["balance"] += amount
        self._record(uid, "credit", amount, credit["balance"], reason=f"card purchase: {reason}")
        self._commit(uid, profile)
        return credit["balance"]

    def hold_card(self, user_id, reason: str, by) -> dict:
        """A banker's manual hold: the card is declined until a banker releases it. Paying the card doesn't lift it."""
        uid = str(user_id)
        profile = self._profile(uid)
        if profile["credit"].get("hold"):
            raise BankRefused("❌ That card is already on hold.")
        profile["credit"]["hold"] = {"reason": reason, "by": str(by), "at": self._clock.now().isoformat(timespec="seconds")}
        self._commit(uid, profile)
        return profile["credit"]["hold"]

    def release_card(self, user_id) -> dict:
        uid = str(user_id)
        profile = self._profile(uid)
        hold = profile["credit"].get("hold")
        if not hold:
            raise BankRefused("❌ That card isn't on hold.")
        profile["credit"]["hold"] = None
        self._commit(uid, profile)
        return hold

    def pay_card(self, user_id, amount: Optional[int], source: str = "wallet") -> int:
        uid = str(user_id)
        profile = self._profile(uid)
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
        self._commit(uid, profile)
        return amount

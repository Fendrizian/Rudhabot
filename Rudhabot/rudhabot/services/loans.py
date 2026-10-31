"""Loan rules: taking, extending and repaying loans, and the periodic settlement / warning passes.

Methods raise `LoanRefused(code, **data)` for user-facing problems; the cog turns codes into messages. The
background passes (`settle_due_loans`, `pending_warnings`) never touch Discord: they update accounts in ONE
transaction and return events for the caller to announce afterwards. (The old tasks held a snapshot of every
account across slow `await`s and then saved it, overwriting anything users did meanwhile.)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Union

from rudhabot.clock import Clock
from rudhabot.domain.loan import Loan
from rudhabot.persistence.users import UserRepository
from rudhabot.settings import LoanTerms


class LoanRefused(Exception):
    """A request the rules reject. `code` identifies why; `data` carries values for the message."""

    def __init__(self, code: str, **data):
        super().__init__(code)
        self.code = code
        self.data = data


@dataclass(frozen=True)
class LoanOpened:
    loan: Loan
    due: datetime


@dataclass(frozen=True)
class LoanExtended:
    days: int
    fee: int


@dataclass(frozen=True)
class LoanRepayment:
    paid: int
    remaining: int
    cleared: bool


@dataclass(frozen=True)
class AutoPaid:
    user_id: int
    total_due: int


@dataclass(frozen=True)
class Bankruptcy:
    user_id: int
    total_due: int
    ban_days: int
    ban_until: datetime


@dataclass(frozen=True)
class DueSoon:
    user_id: int
    total_due: int
    due: datetime


SettlementEvent = Union[AutoPaid, Bankruptcy]


class LoanService:
    #: warn when a loan falls due between 12 and 36 hours from now (the hourly job's window)
    WARNING_WINDOW = (12 * 3600, 36 * 3600)

    def __init__(self, users: UserRepository, terms: LoanTerms, clock: Clock):
        self._users = users
        self._terms = terms
        self._clock = clock

    # -------------------------------------------------------------- queries
    def active_loan(self, user_id: int | str) -> Optional[Loan]:
        return Loan.from_account(self._users.get(user_id), self._terms.interest_rate)

    # ------------------------------------------------------------ commands
    def open(self, user_id: int | str, amount: int, days: int) -> LoanOpened:
        account = self._users.get(user_id)
        if Loan.from_account(account, self._terms.interest_rate):
            raise LoanRefused("already_has_loan")
        self._check_ban(user_id, account)
        t = self._terms
        if amount < t.min_amount or amount > t.max_amount or days < t.min_days or days > t.max_days:
            raise LoanRefused("invalid_terms")
        now = self._clock.now()
        loan = Loan.open(amount, days, t.interest_rate, now)
        # a new loan also resets the "due soon" warning flag
        self._users.update(user_id, wallet=account["wallet"] + amount, loan=loan.to_dict(), loan_warning_sent=False)
        return LoanOpened(loan, now + timedelta(days=days))

    def extend(self, user_id: int | str, days: int) -> LoanExtended:
        account = self._users.get(user_id)
        loan = Loan.from_account(account, self._terms.interest_rate)
        t = self._terms
        if not loan:
            raise LoanRefused("no_loan_to_extend")
        if loan.interest_days >= t.max_days:
            raise LoanRefused("at_max_days", max_days=t.max_days)
        if loan.extended:
            raise LoanRefused("already_extended")
        if loan.interest_days + days > t.max_days:
            raise LoanRefused("exceeds_limit", allowed=t.max_days - loan.interest_days)
        fee = loan.extension_fee(t.extension_fee_rate)
        if account["wallet"] < fee:
            raise LoanRefused("cannot_afford_fee", fee=fee)
        loan.extend(days)
        self._users.update(user_id, wallet=account["wallet"] - fee, loan=loan.to_dict())
        return LoanExtended(days, fee)

    def repay(self, user_id: int | str, amount_text: str) -> LoanRepayment:
        account = self._users.get(user_id)
        loan = Loan.from_account(account, self._terms.interest_rate)
        if not loan:
            raise LoanRefused("no_loan_to_pay")
        now = self._clock.now()
        total_due = loan.total_due(now)
        wallet = account.get("wallet", 0)
        if amount_text.lower() == "all":
            pay_amount = min(wallet, total_due)
        else:
            try:
                pay_amount = int(amount_text)
            except ValueError:
                raise LoanRefused("invalid_amount") from None
        if pay_amount <= 0:
            raise LoanRefused("not_positive")
        if pay_amount > wallet:
            raise LoanRefused("insufficient_wallet")
        pay_amount = min(pay_amount, total_due)       # never burn money by overpaying
        remaining = loan.repay(pay_amount, now)
        if remaining <= 0:
            self._users.update(user_id, wallet=wallet - pay_amount, loan=None)
            return LoanRepayment(pay_amount, 0, cleared=True)
        self._users.update(user_id, wallet=wallet - pay_amount, loan=loan.to_dict())
        return LoanRepayment(pay_amount, remaining, cleared=False)

    def _check_ban(self, user_id, account: dict) -> None:
        ban = account.get("loan_ban_until")
        if not ban:
            return
        try:
            ban_until = datetime.fromisoformat(ban)
        except (ValueError, TypeError):
            self._users.update(user_id, loan_ban_until=None)       # unreadable ban date: drop it
            return
        now = self._clock.now()
        if now < ban_until:
            raise LoanRefused("banned", until=ban_until, days_remaining=(ban_until - now).days + 1)
        self._users.update(user_id, loan_ban_until=None)           # ban expired

    # ------------------------------------------------- periodic maintenance
    def settle_due_loans(self) -> list[SettlementEvent]:
        """Collect every overdue loan: auto-pay from wallet then bank, or declare bankruptcy."""
        now = self._clock.now()
        events: list[SettlementEvent] = []
        with self._users.transaction() as accounts:
            for user_id, data in accounts.items():
                loan = Loan.from_dict(data.get("loan"), self._terms.interest_rate)
                if not loan or not loan.is_due(now):
                    continue
                total_due = loan.total_due(now)
                wallet, bank = data.get("wallet", 0), data.get("bank", 0)
                if int(round(wallet + bank)) >= total_due:
                    if wallet >= total_due:
                        data["wallet"] = int(round(wallet - total_due))
                    else:
                        shortfall = int(round(total_due - wallet))
                        data["wallet"] = 0
                        data["bank"] = int(round(bank - shortfall))
                    data["loan"] = None
                    data["loan_warning_sent"] = False
                    events.append(AutoPaid(int(user_id), total_due))
                else:
                    ban_days = max(1, int(round(total_due / self._terms.ban_divisor)))
                    ban_until = now + timedelta(days=ban_days)
                    data["wallet"], data["bank"], data["loan"] = 0, 0, None
                    data["loan_ban_until"] = ban_until.isoformat()
                    data["loan_warning_sent"] = False
                    events.append(Bankruptcy(int(user_id), total_due, ban_days, ban_until))
        return events

    def pending_warnings(self) -> list[DueSoon]:
        """Loans falling due soon whose owner has not been warned yet (read-only)."""
        now = self._clock.now()
        low, high = self.WARNING_WINDOW
        warnings = []
        for user_id, data in self._users.all().items():
            loan = Loan.from_dict(data.get("loan"), self._terms.interest_rate)
            if not loan or data.get("loan_warning_sent"):
                continue
            if low <= (loan.due_at - now).total_seconds() <= high:
                warnings.append(DueSoon(int(user_id), loan.total_due(now), loan.due_at))
        return warnings

    def mark_warned(self, user_id: int | str) -> None:
        self._users.update(user_id, loan_warning_sent=True)

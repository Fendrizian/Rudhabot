"""The loan domain model. Dict (de)serialisation is unchanged, so existing data.json loans keep working."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


class Loan:
    def __init__(
        self,
        principal: int,
        interest_rate: float,
        start_date: str,
        due_date: str,
        interest_days: int,
        extended: bool = False,
        last_interest_update: str = None,
    ):
        self.principal = principal
        self.interest_rate = interest_rate
        self.start_date = start_date
        self.due_date = due_date
        self.interest_days = interest_days
        self.extended = extended
        self.last_interest_update = last_interest_update or start_date

    # ------------------------------------------------------------- construction
    @staticmethod
    def from_dict(data: dict, default_rate: float = 0.02) -> Optional["Loan"]:
        if not data:
            return None
        return Loan(
            principal=data.get("principal", 0),
            interest_rate=data.get("interest_rate", default_rate),
            start_date=data.get("start_date"),
            due_date=data.get("due"),
            interest_days=data.get("interest_days", 0),
            extended=data.get("extended", False),
            last_interest_update=data.get("last_interest_update"),
        )

    @classmethod
    def from_account(cls, account: dict, default_rate: float = 0.02) -> Optional["Loan"]:
        return cls.from_dict(account.get("loan"), default_rate)

    @classmethod
    def open(cls, principal: int, days: int, interest_rate: float, now: datetime) -> "Loan":
        return cls(
            principal=principal,
            interest_rate=interest_rate,
            start_date=now.isoformat(),
            due_date=(now + timedelta(days=days)).isoformat(),
            interest_days=days,
            extended=False,
            last_interest_update=now.isoformat(),
        )

    def to_dict(self) -> dict:
        return {
            "principal": self.principal,
            "interest_rate": self.interest_rate,
            "start_date": self.start_date,
            "due": self.due_date,
            "interest_days": self.interest_days,
            "extended": self.extended,
            "last_interest_update": self.last_interest_update,
        }

    # ----------------------------------------------------------------- queries
    @property
    def due_at(self) -> datetime:
        return datetime.fromisoformat(self.due_date)

    def accrued_days(self, now: Optional[datetime] = None) -> int:
        start = datetime.fromisoformat(self.start_date)
        days = ((now or datetime.now()) - start).days
        return max(0, min(days, self.interest_days))

    def total_due(self, now: Optional[datetime] = None) -> int:
        return int(round(self.principal * ((1 + self.interest_rate) ** self.accrued_days(now))))

    def is_due(self, now: Optional[datetime] = None) -> bool:
        return (now or datetime.now()) >= self.due_at

    def extension_fee(self, fee_rate: float) -> int:
        return int(round(self.principal * fee_rate))

    # --------------------------------------------------------------- mutations
    def extend(self, extra_days: int) -> None:
        self.interest_days += extra_days
        self.due_date = (datetime.fromisoformat(self.due_date) + timedelta(days=extra_days)).isoformat()
        self.extended = True

    def repay(self, amount: int, now: Optional[datetime] = None) -> int:
        """Repay the loan. Returns remaining debt after payment."""
        total_due = self.total_due(now)
        remaining = total_due - amount
        if remaining <= 0:
            return 0
        days = self.accrued_days(now)
        interest_factor = (1 + self.interest_rate) ** days
        self.principal = int(round(remaining / interest_factor))
        return int(round(remaining))

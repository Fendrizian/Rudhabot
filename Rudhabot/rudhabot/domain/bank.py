"""Bank rules: card tiers, interest on the bank balance and credit-card statements. Pure functions, no Discord, no files."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


@dataclass(frozen=True)
class Tier:
    key: str
    name: str
    emoji: str
    daily_interest: float        # interest on the bank balance per day (0.0005 = 0.05%)
    monthly_limit: int           # max deposited AND max withdrawn (wallet <-> bank) per calendar month, each
    credit_limit: int
    upgrade_fee: int             # paid from the bank balance to reach this tier
    net_worth_required: int      # wallet + bank needed to upgrade to this tier

    @property
    def interest_cap(self) -> int:
        """Bank balance above this earns no interest (keeps high tiers from printing money)."""
        return self.monthly_limit * 5


TIERS: tuple[Tier, ...] = (
    Tier("red", "Red", "<:bankcard_red:1417887131510116483>", 0.0005, 10_000, 2_000, 0, 0),
    Tier("silver", "Silver", "<:bankcard_silver:1417887151156236480>", 0.001, 20_000, 5_000, 2_500, 25_000),
    Tier("gold", "Gold", "<:bankcard_gold:1417887174895861792>", 0.002, 50_000, 15_000, 10_000, 100_000),
    Tier("platinum", "Platinum", "<:bankcard_platinum:1417887196500983950>", 0.003, 100_000, 40_000, 30_000, 300_000),
)
TIER_BY_KEY = {t.key: t for t in TIERS}

LATE_INTEREST = 0.05             # charged once on an unpaid statement after its due date
STATEMENT_GRACE_DAYS = 10        # a statement issued on the 1st is due on the 11th


def next_tier(current: str) -> Optional[Tier]:
    keys = [t.key for t in TIERS]
    i = keys.index(current)
    return TIERS[i + 1] if i + 1 < len(TIERS) else None


def accrue(balance: float, carry: float, tier: Tier, days: int) -> tuple[int, float]:
    """Daily-compounded interest for `days` days on the interest-bearing part of a bank `balance`.

    Returns (whole ra to credit, new fractional carry). Fractions are carried so small balances still earn over time.
    """
    if days <= 0 or balance <= 0:
        return 0, carry
    base = min(balance, tier.interest_cap)
    exact = round(base * ((1 + tier.daily_interest) ** days - 1) + carry, 9)    # rounding: 0.5 + 0.5 must be 1
    whole = math.floor(exact)
    return whole, exact - whole


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def roll_statement(credit: dict, today: date) -> Optional[int]:
    """Bring a credit-card record up to date. Mutates `credit`; returns the late interest charged now, if any.

    * At the first access in a new month, the whole outstanding balance becomes that month's statement, due
      STATEMENT_GRACE_DAYS later.
    * If a statement is still unpaid after its due date, LATE_INTEREST is added once and the card is frozen until the
      statement is paid in full.
    """
    period = month_key(today)
    if credit.get("period") != period:
        credit["period"] = period
        credit["statement"] = int(credit.get("balance", 0))
        credit["due"] = (today.replace(day=1) + timedelta(days=STATEMENT_GRACE_DAYS)).isoformat()
        credit["late_charged"] = False
    if (credit.get("statement", 0) > 0 and not credit.get("late_charged") and credit.get("due")
            and today > date.fromisoformat(credit["due"])):
        interest = math.ceil(credit["statement"] * LATE_INTEREST)
        credit["balance"] = int(credit.get("balance", 0)) + interest
        credit["statement"] += interest
        credit["late_charged"] = True
        credit["frozen"] = True
        return interest
    return None

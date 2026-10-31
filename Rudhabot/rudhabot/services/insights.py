"""Clairvoyance finance insights: one or two quiet, personal observations shown in the bank screens.

Computed only from the person's own ledger entries and bank profile; shown only to that person; advisory only
(nothing is changed, limited or charged because of an insight). Each candidate insight gets a priority and the most
useful one or two are returned.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median

from rudhabot.domain.bank import TIER_BY_KEY, TIERS
from rudhabot.ml.anomaly import GAMBLING

SPENDING_ACCOUNTS = ("wallet", "bank")


def finance_insights(entries: list[dict], profile: dict, bank_balance: int, now: datetime, limit: int = 2) -> list[str]:
    tier = TIER_BY_KEY.get(profile.get("tier"), TIERS[0])
    candidates: list[tuple[int, str]] = []
    # 1. card utilisation and due date
    credit = profile.get("credit", {})
    if tier.credit_limit and credit.get("balance", 0) > 0:
        used = credit["balance"] / tier.credit_limit
        if credit.get("statement", 0) > 0 and credit.get("due"):
            days = (datetime.fromisoformat(credit["due"]).date() - now.date()).days
            if 0 <= days <= 5:
                candidates.append((95, f"Your card statement of {credit['statement']:,} is due in {days} day(s)."))
        if used >= 0.7:
            candidates.append((80, f"Your card is {used:.0%} used; paying it down keeps purchases from being declined."))
    # 2. this week's spending against the person's own usual weekly spending (median of the 4 weeks before)
    weekly = [0.0] * 5
    gambling_week = 0.0
    for e in entries:
        if e.get("account") not in SPENDING_ACCOUNTS:
            continue
        age = (now - datetime.fromisoformat(e["ts"])).days
        if 0 <= age < 35 and e["delta"] < 0 and not str(e.get("reason", "")).startswith(("/deposit", "/withdraw", "/bank")):
            weekly[age // 7] -= e["delta"]
        if 0 <= age < 7 and str(e.get("reason", "")).startswith(GAMBLING):
            gambling_week += e["delta"]
    usual = median(weekly[1:5])
    if usual > 500 and weekly[0] > 2 * usual:
        candidates.append((70, f"You've spent about {weekly[0] / usual:.1f}× your usual weekly amount this week."))
    if gambling_week < -max(2000, 0.5 * (bank_balance + profile.get("savings", 0))):
        candidates.append((60, f"Games of chance are {gambling_week:,.0f} for you this week."))
    # 3. idle money that could be earning interest
    room = max(0, tier.interest_cap - profile.get("savings", 0))
    idle = min(bank_balance, room)
    if idle >= 1000:
        weekly_interest = idle * ((1 + tier.daily_interest) ** 7 - 1)
        if weekly_interest >= 5:
            candidates.append((40, f"{idle:,} in your bank balance could earn about {weekly_interest:,.0f} a week in "
                                   f"savings (`/bank save`)."))
    candidates.sort(key=lambda c: -c[0])
    return [text for _, text in candidates[:limit]]

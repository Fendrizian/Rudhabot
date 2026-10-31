"""The Rudharta Exchange: NAS (The National Shop) and JOOT (Joot Cafe), priced by what the server actually buys.

State lives in `stocks.json`: per listing the price, revenue rate, last tick and a price history; per user the shares
held and their cost basis. Shop purchases call `record_revenue()`; a background task (and every read) calls `tick()`.
Wallet changes go through `UserRepository` (audited automatically); share changes are reported here as
`shares:<SYMBOL>` audit entries.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from rudhabot.clock import Clock
from rudhabot.domain.stocks import LISTINGS, Listing, add_revenue, decay_revenue, fill, fundamental, step_price
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.users import UserRepository

TICK_MINUTES = 15
HISTORY = 288                  # 3 days of 15-minute ticks
MAX_ORDER_FRACTION = 0.05      # one order may trade at most 5% of the shares outstanding
MAX_CATCH_UP_TICKS = 96 * 7    # after downtime, simulate at most a week of ticks
PER_USER_DAILY_FULL = 2_000    # each person's purchases count fully up to this per company per day...
EXCESS_WEIGHT = 0.10           # ...and at 10% beyond it, so broad activity (not one whale) moves the price


class TradeRefused(Exception):
    """A user-facing refusal; the message is shown as-is."""


@dataclass(frozen=True)
class Trade:
    symbol: str
    shares: int
    gross: float
    fee: int
    total: int                 # what the wallet paid (buy) or received (sell)
    price_before: float
    price_after: float


def initial_state(now_iso: str) -> dict:
    return {"listings": {s: {"price": l.ipo, "revenue_rate": l.revenue_ref * 0.5, "last_tick": now_iso,
                             "last_revenue": now_iso, "history": [[now_iso, l.ipo]], "revenue_24h": []}
                         for s, l in LISTINGS.items()},
            "holdings": {}}


class StockExchange:
    def __init__(self, store: JsonStore, users: UserRepository, clock: Clock, rng: random.Random, audit=None):
        self._store, self._users, self._clock, self._rng, self._audit = store, users, clock, rng, audit

    # ------------------------------------------------------------------ state
    def _load(self) -> dict:
        data = self._store.load()
        now = self._clock.now().isoformat(timespec="seconds")
        fresh = initial_state(now)
        for symbol, state in fresh["listings"].items():              # new listings appear automatically
            data.setdefault("listings", {}).setdefault(symbol, state)
        data.setdefault("holdings", {})
        return data

    def listing(self, symbol: str) -> Listing:
        listing = LISTINGS.get(symbol.upper())
        if listing is None:
            raise TradeRefused(f"❌ Unknown stock. Listed: {', '.join(LISTINGS)}.")
        return listing

    # ------------------------------------------------------------------ market simulation
    def tick(self) -> dict:
        """Advance every listing to now in TICK_MINUTES steps. Safe to call often (does nothing between ticks)."""
        data = self._load()
        now = self._clock.now()
        changed = False
        for symbol, listing in LISTINGS.items():
            st = data["listings"][symbol]
            last = datetime.fromisoformat(st["last_tick"])
            steps = int((now - last).total_seconds() // (TICK_MINUTES * 60))
            if steps <= 0:
                continue
            dt = TICK_MINUTES / 1440
            if steps > MAX_CATCH_UP_TICKS:          # long outage: revenue still decays for the whole time
                st["revenue_rate"] = decay_revenue(st["revenue_rate"], (steps - MAX_CATCH_UP_TICKS) * dt,
                                                   listing.half_life_days)
            for _ in range(min(steps, MAX_CATCH_UP_TICKS)):
                st["revenue_rate"] = decay_revenue(st["revenue_rate"], dt, listing.half_life_days)
                value = fundamental(listing, st["revenue_rate"])
                st["price"] = step_price(listing, st["price"], value, dt, self._rng.gauss(0, 1))
            # advance by whole ticks so a partial interval isn't lost; after a long outage, catch up to now
            advanced = now if steps > MAX_CATCH_UP_TICKS else last + timedelta(minutes=TICK_MINUTES * steps)
            st["last_tick"] = advanced.isoformat(timespec="seconds")
            st["history"] = (st["history"] + [[st["last_tick"], round(st["price"], 4)]])[-HISTORY:]
            changed = True
        if changed:
            self._store.save(data)
        return data

    def record_revenue(self, symbol: str, amount: float, user_id=None) -> float:
        """A purchase (positive) or a buy-back (negative) at the company behind `symbol`. Returns the amount that
        counted toward revenue after the per-person daily weighting."""
        if not amount or symbol not in LISTINGS:
            return 0.0
        data = self.tick()
        listing = LISTINGS[symbol]
        st = data["listings"][symbol]
        now = self._clock.now()
        counted = float(amount)
        if amount > 0 and user_id is not None:
            day = now.date().isoformat()
            spend = st.setdefault("spend", {})
            spend = {u: v for u, v in spend.items() if v[0] == day}           # forget earlier days
            st["spend"] = spend
            before = spend.get(str(user_id), [day, 0.0])[1]
            full = max(0.0, min(amount, PER_USER_DAILY_FULL - before))
            counted = full + (amount - full) * EXCESS_WEIGHT
            spend[str(user_id)] = [day, before + amount]
        if counted > 0:
            st["revenue_rate"] = add_revenue(st["revenue_rate"], counted, listing.half_life_days)
        else:
            st["revenue_rate"] = max(0.0, st["revenue_rate"] + counted * math.log(2) / listing.half_life_days)
        cutoff = now.timestamp() - 86400
        st["revenue_24h"] = [r for r in st.get("revenue_24h", []) if r[0] >= cutoff] + [[now.timestamp(), amount]]
        self._store.save(data)
        return counted

    def quote(self, symbol: str) -> dict:
        listing = self.listing(symbol)
        st = self.tick()["listings"][listing.symbol]
        history = st["history"]
        day_ago = self._clock.now().timestamp() - 86400
        past = next((p for t, p in history if datetime.fromisoformat(t).timestamp() >= day_ago), history[0][1])
        cutoff = self._clock.now().timestamp() - 86400
        return {"symbol": listing.symbol, "name": listing.name, "price": st["price"], "change_24h": st["price"] / past - 1,
                "value": fundamental(listing, st["revenue_rate"]), "revenue_rate": st["revenue_rate"],
                "revenue_24h": sum(a for t, a in st.get("revenue_24h", []) if t >= cutoff),
                "market_cap": st["price"] * listing.shares, "history": [p for _, p in history]}

    # ------------------------------------------------------------------ trading
    def _holding(self, data: dict, uid: str, symbol: str) -> dict:
        return data["holdings"].setdefault(uid, {}).setdefault(symbol, {"shares": 0, "cost": 0.0})

    def preview(self, symbol: str, shares: int, side: str) -> Trade:
        listing = self.listing(symbol)
        price = self.tick()["listings"][listing.symbol]["price"]
        gross, after = fill(listing, price, shares, side)
        fee = max(1, math.ceil(gross * listing.fee))
        total = math.ceil(gross) + fee if side == "buy" else max(0, math.floor(gross) - fee)
        return Trade(listing.symbol, shares, gross, fee, total, price, after)

    def buy(self, user_id, symbol: str, shares: int) -> Trade:
        uid = str(user_id)
        listing = self.listing(symbol)
        if shares < 1:
            raise TradeRefused("❌ Buy at least 1 share.")
        if shares > listing.shares * MAX_ORDER_FRACTION:
            raise TradeRefused(f"❌ One order can trade at most {int(listing.shares * MAX_ORDER_FRACTION):,} shares.")
        trade = self.preview(listing.symbol, shares, "buy")
        acc = self._users.get(uid)
        if acc["wallet"] < trade.total:
            raise TradeRefused(f"❌ That costs {trade.total:,} (incl. {trade.fee:,} fee); your wallet has {acc['wallet']:,}.")
        data = self.tick()
        self._users.update(uid, wallet=acc["wallet"] - trade.total)
        hold = self._holding(data, uid, listing.symbol)
        hold["shares"] += shares
        hold["cost"] += trade.total
        data["listings"][listing.symbol]["price"] = trade.price_after
        self._store.save(data)
        self._record(uid, listing.symbol, shares, hold["shares"], f"bought {shares} {listing.symbol} for {trade.total:,}")
        return trade

    def sell(self, user_id, symbol: str, shares: Optional[int]) -> Trade:
        uid = str(user_id)
        listing = self.listing(symbol)
        data = self.tick()
        hold = self._holding(data, uid, listing.symbol)
        shares = hold["shares"] if shares is None else shares
        if shares < 1 or shares > hold["shares"]:
            raise TradeRefused(f"❌ You hold {hold['shares']:,} {listing.symbol}.")
        if shares > listing.shares * MAX_ORDER_FRACTION:
            raise TradeRefused(f"❌ One order can trade at most {int(listing.shares * MAX_ORDER_FRACTION):,} shares.")
        trade = self.preview(listing.symbol, shares, "sell")
        data = self.tick()
        hold = self._holding(data, uid, listing.symbol)
        hold["cost"] = hold["cost"] * (1 - shares / hold["shares"])
        hold["shares"] -= shares
        data["listings"][listing.symbol]["price"] = trade.price_after
        self._store.save(data)
        acc = self._users.get(uid)
        self._users.update(uid, wallet=acc["wallet"] + trade.total)
        self._record(uid, listing.symbol, -shares, hold["shares"], f"sold {shares} {listing.symbol} for {trade.total:,}")
        return trade

    def portfolio(self, user_id) -> list[dict]:
        data = self.tick()
        out = []
        for symbol, hold in data["holdings"].get(str(user_id), {}).items():
            if hold["shares"] <= 0:
                continue
            price = data["listings"][symbol]["price"]
            value = price * hold["shares"]
            out.append({"symbol": symbol, "shares": hold["shares"], "price": price, "value": value,
                        "cost": hold["cost"], "pnl": value - hold["cost"]})
        return out

    def _record(self, uid: str, symbol: str, delta: int, after: int, reason: str) -> None:
        if self._audit is not None:
            self._audit.record(uid, f"shares:{symbol}", delta, after, reason=reason)

"""Stock-market maths for the Rudharta Exchange. Pure functions; all randomness is passed in.

Model (per listed company)
  revenue    Every purchase at the company (shop -> NAS, café -> JOOT) adds to an exponentially-weighted revenue rate
             R (ra per day, half-life `half_life_days`). Sales back to the shop subtract (never below zero).
  value      Fundamental value  V = ipo * (base + (1 - base) * sqrt(R / R_ref)), capped at `max_multiple` * ipo.
             The square root gives diminishing returns; `base` (> 0) is the value of the company's assets with no
             sales at all, so V can never reach zero: there is no default.
  price      Every tick the log-price mean-reverts toward log V (Ornstein-Uhlenbeck, speed `kappa` per day) with
             daily volatility `sigma`, and can never go below `floor` * ipo.
  trading    An order of q shares moves the price by exp(±impact * q / depth) and fills at the *average* price along
             that path, plus a fee. Buying then immediately selling therefore always loses the fee and the spread;
             impact then decays as the price mean-reverts to V. This is what makes pump-and-dump unprofitable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Listing:
    symbol: str
    name: str
    ipo: float                    # initial / reference price
    revenue_ref: float            # daily revenue at which V == ipo
    shares: int                   # shares outstanding
    base: float = 0.35            # share of value that doesn't depend on sales (assets): the anti-default floor
    max_multiple: float = 8.0
    floor: float = 0.10           # hard price floor as a fraction of ipo
    half_life_days: float = 3.0
    kappa: float = 0.35           # mean reversion per day
    sigma: float = 0.03           # daily volatility of the log-price
    impact: float = 0.6           # log-price move when trading `depth` shares
    depth_fraction: float = 0.02  # depth = shares * depth_fraction
    fee: float = 0.005

    @property
    def depth(self) -> float:
        return self.shares * self.depth_fraction


LISTINGS: dict[str, Listing] = {
    "NAS": Listing("NAS", "The National Shop", ipo=100.0, revenue_ref=20_000, shares=1_000_000),
    "JOOT": Listing("JOOT", "Joot Cafe", ipo=80.0, revenue_ref=8_000, shares=800_000),
}


def decay_revenue(rate: float, days: float, half_life: float) -> float:
    return rate * 0.5 ** (days / half_life) if days > 0 else rate


def add_revenue(rate: float, amount: float, half_life: float) -> float:
    """Add a one-off amount to an EWMA *daily* rate (the impulse that integrates to `amount` over time)."""
    return max(0.0, rate + amount * math.log(2) / half_life)


def fundamental(listing: Listing, revenue_rate: float) -> float:
    growth = math.sqrt(max(0.0, revenue_rate) / listing.revenue_ref)
    value = listing.ipo * (listing.base + (1 - listing.base) * growth)
    return min(value, listing.ipo * listing.max_multiple)


def step_price(listing: Listing, price: float, value: float, dt_days: float, shock: float) -> float:
    """One OU step in log space. `shock` is a standard-normal draw supplied by the caller."""
    log_p, log_v = math.log(price), math.log(max(value, listing.ipo * listing.floor))
    decay = math.exp(-listing.kappa * dt_days)
    mean = log_v + (log_p - log_v) * decay
    std = listing.sigma * math.sqrt((1 - decay * decay) / (2 * listing.kappa)) if listing.kappa else listing.sigma * math.sqrt(dt_days)
    return max(listing.ipo * listing.floor, math.exp(mean + std * shock))


def fill(listing: Listing, price: float, shares: int, side: str) -> tuple[float, float]:
    """(gross cost or proceeds, price after the trade) for buying/selling `shares` along the impact curve."""
    k = listing.impact / listing.depth
    if side == "buy":
        after = price * math.exp(k * shares)
        gross = (after - price) / k if k else price * shares
    else:
        after = max(listing.ipo * listing.floor, price * math.exp(-k * shares))
        gross = (price - after) / k if k else price * shares
    return gross, after

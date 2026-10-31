"""Coin definitions."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoinSpec:
    symbol: str
    name: str
    volatility: float


DEFAULT_COINS = (
    CoinSpec("SCAY", "Sachen's & Atty's", 0.03),
    CoinSpec("RDC", "Royal Digital Currency", 0.05),
    CoinSpec("LICH", "Lichtensvonium", 0.10),
    CoinSpec("WNC", "Warnacoin", 0.12),
)

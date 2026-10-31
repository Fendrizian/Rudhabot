"""The slot machine rules.

Technicalities (kept exactly as the original command): three independent reels, each drawn with
`rng.choices(symbols, weights=weights)`; three of a kind pays the symbol's multiplier, any other pair pays x2,
everything else loses the bet. This class only *models* those rules and adds exact analytics; the animation and
embeds live in the presentation layer.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from itertools import product
from typing import Optional, Sequence


@dataclass(frozen=True)
class SlotSymbol:
    key: str
    name: str
    emoji: str
    weight: int
    triple_multiplier: int


# Order matters: it fixes how the weighted draw maps random numbers to symbols (and so how seeds replay).
DEFAULT_SYMBOLS: tuple[SlotSymbol, ...] = (
    SlotSymbol("cherries", "Cherries", "<:slot_cherries:1398958979513585695>", 30, 3),
    SlotSymbol("lemon", "Lemons", "<:slot_lemon:1398953879894233190>", 25, 5),
    SlotSymbol("orange", "Oranges", "<:slot_orange:1398958425420988519>", 20, 8),
    SlotSymbol("grapes", "Grapes", "<:slot_grapes:1398958399194009621>", 15, 10),
    SlotSymbol("diamond", "Diamonds", "<:slot_diamond:1398954988142592060>", 5, 15),
    SlotSymbol("hibiscus", "Hibiscus", "<:slot_hibiscus:1399010286144585760>", 3, 20),
    SlotSymbol("seven", "Sevens", "<:slot_seven:1398952681371861022>", 2, 50),
)

PAIR_MULTIPLIER = 2


class SlotKind(Enum):
    TRIPLE = "triple"
    PAIR = "pair"
    MISS = "miss"


@dataclass(frozen=True)
class SlotOutcome:
    kind: SlotKind
    multiplier: int
    symbol: Optional[SlotSymbol] = None   # the matching symbol for a triple or pair

    @property
    def won(self) -> bool:
        return self.multiplier > 0

    def payout(self, bet: int) -> int:
        """Total returned to the player (stake included)."""
        return bet * self.multiplier

    def net(self, bet: int) -> int:
        """Change in the player's wallet."""
        return self.payout(bet) - bet if self.won else -bet


class SlotMachine:
    REELS = 3

    def __init__(
        self,
        symbols: Sequence[SlotSymbol] = DEFAULT_SYMBOLS,
        pair_multiplier: int = PAIR_MULTIPLIER,
        rng: random.Random = random,
    ):
        self.symbols = tuple(symbols)
        self.pair_multiplier = pair_multiplier
        self._weights = [s.weight for s in self.symbols]
        self._rng = rng

    # ------------------------------------------------------------------ play
    def spin_reel(self) -> SlotSymbol:
        """Draw one reel. One call = one RNG draw, in the same way the original command drew it."""
        return self._rng.choices(self.symbols, weights=self._weights)[0]

    def evaluate(self, reels: Sequence[SlotSymbol]) -> SlotOutcome:
        a, b, c = reels
        if a == b == c:
            return SlotOutcome(SlotKind.TRIPLE, a.triple_multiplier, a)
        if a == b or b == c or a == c:
            matching = a if (a == b or a == c) else b
            return SlotOutcome(SlotKind.PAIR, self.pair_multiplier, matching)
        return SlotOutcome(SlotKind.MISS, 0)

    # ------------------------------------------------------------- analytics
    def _probabilities(self):
        total = sum(self._weights)
        return {s: Fraction(s.weight, total) for s in self.symbols}

    def outcome_distribution(self) -> dict[tuple[SlotKind, int], Fraction]:
        """Exact probability of every (kind, multiplier) outcome."""
        probs = self._probabilities()
        dist: dict[tuple[SlotKind, int], Fraction] = {}
        for reels in product(self.symbols, repeat=self.REELS):
            p = Fraction(1)
            for symbol in reels:
                p *= probs[symbol]
            outcome = self.evaluate(reels)
            key = (outcome.kind, outcome.multiplier)
            dist[key] = dist.get(key, Fraction(0)) + p
        return dist

    def return_to_player(self) -> Fraction:
        """Expected payout per unit staked (1 = break-even; above 1 the player profits on average)."""
        return sum(p * mult for (_, mult), p in self.outcome_distribution().items())

    def hit_rate(self) -> Fraction:
        return sum(p for (_, mult), p in self.outcome_distribution().items() if mult > 0)

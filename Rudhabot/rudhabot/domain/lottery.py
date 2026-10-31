"""Lottery tickets: exact prize tables, validated invariants, and drawing.

How this differs from the slot machine's weighting
--------------------------------------------------
Slots draw floating-point weights with `random.choices`. The lottery instead uses *exact integer weights* over a
fixed denominator of 10,000,000 outcomes. A single `randrange(10_000_000)` roll is mapped onto cumulative bounds
with a binary search, so every probability is an exact fraction (jackpot = 200 / 10,000,000), no float rounding
can creep in, and expected value / variance can be computed exactly and verified in tests.

Design rules, enforced when a `TicketSpec` is built (a mis-tuned table cannot even load):
  * the jackpot is the largest prize and is rarer than 0.005%;
  * "zonk" (no prize) is more likely than winning anything;
  * rarer prizes are strictly bigger (probability strictly falls as the prize rises).

Prize ladders were solved to a power-law decay (probability ~ prize**-alpha) targeting an ~80% return to
player, so tickets are a money *sink*. The jackpot alone contributes 20% of that return.
"""
from __future__ import annotations

import random
from bisect import bisect_right
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import accumulate
from typing import Mapping, NamedTuple, Optional, Sequence

DENOMINATOR = 10_000_000
MAX_JACKPOT_PROBABILITY = Fraction(5, 100_000)   # 0.005%


@dataclass(frozen=True)
class PrizeTier:
    amount: int
    weight: int          # out of the table's denominator


class PrizeTable:
    """An exact discrete distribution: each tier's weight, with the remainder of the denominator = zonk."""

    def __init__(self, tiers: Sequence[PrizeTier], denominator: int = DENOMINATOR):
        tiers = tuple(tiers)
        if not tiers:
            raise ValueError("a prize table needs at least one tier")
        amounts = [t.amount for t in tiers]
        if amounts != sorted(set(amounts)) or amounts[0] <= 0:
            raise ValueError("tier amounts must be positive, unique and in ascending order")
        if any(t.weight <= 0 or int(t.weight) != t.weight for t in tiers):
            raise ValueError("tier weights must be positive integers")
        if sum(t.weight for t in tiers) >= denominator:
            raise ValueError("tier weights must leave room for the zonk outcome")
        self.tiers = tiers
        self.denominator = denominator
        self._bounds = list(accumulate(t.weight for t in tiers))

    # ---------------------------------------------------------------- drawing
    def draw(self, rng: random.Random = random) -> Optional[PrizeTier]:
        """One uniform roll in [0, denominator); None means zonk."""
        index = bisect_right(self._bounds, rng.randrange(self.denominator))
        return self.tiers[index] if index < len(self.tiers) else None

    # -------------------------------------------------------------- analytics
    @property
    def win_weight(self) -> int:
        return self._bounds[-1]

    @property
    def zonk_weight(self) -> int:
        return self.denominator - self.win_weight

    def probability(self, tier: PrizeTier) -> Fraction:
        return Fraction(tier.weight, self.denominator)

    @property
    def win_probability(self) -> Fraction:
        return Fraction(self.win_weight, self.denominator)

    @property
    def zonk_probability(self) -> Fraction:
        return Fraction(self.zonk_weight, self.denominator)

    def expected_value(self) -> Fraction:
        return sum((self.probability(t) * t.amount for t in self.tiers), Fraction(0))

    def variance(self) -> Fraction:
        mean = self.expected_value()
        return sum((self.probability(t) * t.amount ** 2 for t in self.tiers), Fraction(0)) - mean ** 2


@dataclass(frozen=True)
class TicketSpec:
    item: str            # canonical catalog item name
    label: str
    price: int
    table: PrizeTable

    def __post_init__(self):
        table = self.table
        if table.probability(table.tiers[-1]) >= MAX_JACKPOT_PROBABILITY:
            raise ValueError(f"{self.item}: jackpot must be rarer than {float(MAX_JACKPOT_PROBABILITY):.3%}")
        if table.zonk_weight <= table.win_weight:
            raise ValueError(f"{self.item}: a zonk must be more likely than winning anything")
        weights = [t.weight for t in table.tiers]
        if any(weights[i] <= weights[i + 1] for i in range(len(weights) - 1)):
            raise ValueError(f"{self.item}: bigger prizes must be strictly rarer")

    @property
    def jackpot(self) -> int:
        return self.table.tiers[-1].amount

    @property
    def minimum_prize(self) -> int:
        return self.table.tiers[0].amount

    @property
    def jackpot_probability(self) -> Fraction:
        return self.table.probability(self.table.tiers[-1])

    @property
    def return_to_player(self) -> Fraction:
        return self.table.expected_value() / self.price

    def is_jackpot(self, amount: int) -> bool:
        return amount == self.jackpot


def _ladder(amounts: Sequence[int], weights: Sequence[int]) -> PrizeTable:
    return PrizeTable([PrizeTier(a, w) for a, w in zip(amounts, weights, strict=True)])


#                       price 100 | min prize 10 | JACKPOT 1,000,000 (0.002%)  | zonk 64.0% | RTP ~80.0%
REGULAR_TICKET = TicketSpec(
    item="regular lottery ticket", label="Regular", price=100,
    table=_ladder(
        [10, 20, 50, 100, 200, 500, 1_000, 2_500, 10_000, 50_000, 250_000, 1_000_000],
        [1_610_981, 941_485, 462_847, 270_496, 158_082, 77_716, 45_418, 22_328, 7_626, 2_191, 630, 200],
    ),
)

#                       price 1,000 | min prize 100 | JACKPOT 100,000,000 (0.0002%) | zonk 64.0% | RTP ~80.0%
GOLDEN_TICKET = TicketSpec(
    item="golden lottery ticket", label="Golden", price=1_000,
    table=_ladder(
        [100, 200, 500, 1_000, 2_000, 5_000, 10_000, 25_000, 100_000, 500_000, 2_500_000, 10_000_000, 100_000_000],
        [1_675_225, 948_673, 447_361, 253_339, 143_465, 67_653, 38_312, 18_066, 5_794, 1_547, 413, 132, 20],
    ),
)

DEFAULT_SPECS = (REGULAR_TICKET, GOLDEN_TICKET)


class TicketDraw(NamedTuple):
    amount: int          # 0 means zonk
    jackpot: bool


@dataclass
class DrawBatch:
    """Aggregated result of drawing `tickets` tickets of one kind."""
    spec: TicketSpec
    tickets: int = 0
    wins: dict = field(default_factory=dict)      # prize amount -> how many times it hit
    zonks: int = 0

    @property
    def total_won(self) -> int:
        return sum(amount * count for amount, count in self.wins.items())

    @property
    def winning_tickets(self) -> int:
        return sum(self.wins.values())

    @property
    def jackpots(self) -> int:
        return self.wins.get(self.spec.jackpot, 0)

    @property
    def net(self) -> int:
        return self.total_won - self.tickets * self.spec.price


class Lottery:
    """All ticket types and the drawing logic."""

    def __init__(self, specs: Sequence[TicketSpec] = DEFAULT_SPECS, rng: random.Random = random):
        self._specs: Mapping[str, TicketSpec] = {s.item: s for s in specs}
        self._rng = rng

    @property
    def specs(self) -> tuple[TicketSpec, ...]:
        return tuple(self._specs.values())

    def is_ticket(self, item: str) -> bool:
        return item in self._specs

    def spec_for(self, item: str) -> TicketSpec:
        return self._specs[item]

    def draw(self, item: str) -> TicketDraw:
        spec = self._specs[item]
        tier = spec.table.draw(self._rng)
        if tier is None:
            return TicketDraw(0, False)
        return TicketDraw(tier.amount, spec.is_jackpot(tier.amount))

    def draw_many(self, item: str, quantity: int) -> DrawBatch:
        batch = DrawBatch(self._specs[item], tickets=quantity)
        for _ in range(quantity):
            result = self.draw(item)
            if result.amount:
                batch.wins[result.amount] = batch.wins.get(result.amount, 0) + 1
            else:
                batch.zonks += 1
        return batch

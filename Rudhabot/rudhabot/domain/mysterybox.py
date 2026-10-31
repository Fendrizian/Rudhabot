"""Mystery box outcome distribution (unchanged odds and RNG draw order)."""
from __future__ import annotations

import random
from typing import Mapping, NamedTuple, Optional, Union


class MysteryOutcome(NamedTuple):
    kind: str                      # "item" | "money" | "zonk"
    value: Optional[Union[str, int]]


class MysteryBox:
    MONEY_CHANCE = 0.10
    ZONK_CHANCE = 0.15
    CHEAP_ITEMS_CHANCE = 0.70
    EXPENSIVE_ITEMS_CHANCE = 0.05
    MONEY_RANGE = (1, 1000)

    def __init__(self, prize_pool: Mapping[str, int], box_price: int = 300, rng: random.Random = random):
        self._rng = rng
        cheap = {k: v for k, v in prize_pool.items() if v <= box_price}
        expensive = {k: v for k, v in prize_pool.items() if v > box_price}
        self._cheap_names, self._cheap_weights = self._weigh(cheap)
        self._expensive_names, self._expensive_weights = self._weigh(expensive)
        self._categories = []
        self._category_weights = []
        if cheap:
            self._categories.append("cheap_item")
            self._category_weights.append(self.CHEAP_ITEMS_CHANCE)
        if expensive:
            self._categories.append("expensive_item")
            self._category_weights.append(self.EXPENSIVE_ITEMS_CHANCE)
        self._categories += ["money", "zonk"]
        self._category_weights += [self.MONEY_CHANCE, self.ZONK_CHANCE]

    @staticmethod
    def _weigh(items: Mapping[str, int]):
        total = sum(items.values())
        return list(items.keys()), [v / total if total > 0 else 0 for v in items.values()]

    def draw(self) -> MysteryOutcome:
        category = self._rng.choices(self._categories, weights=self._category_weights, k=1)[0]
        if category == "cheap_item":
            return MysteryOutcome("item", self._rng.choices(self._cheap_names, weights=self._cheap_weights, k=1)[0])
        if category == "expensive_item":
            return MysteryOutcome("item", self._rng.choices(self._expensive_names, weights=self._expensive_weights, k=1)[0])
        if category == "money":
            return MysteryOutcome("money", self._rng.randint(*self.MONEY_RANGE))
        return MysteryOutcome("zonk", None)

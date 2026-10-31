"""Stock levels for the cafe's monthly special items (specials.json)."""
from __future__ import annotations

from typing import Iterable, Mapping

from rudhabot.persistence.json_store import JsonStore


class SpecialStockRepository:
    def __init__(self, store: JsonStore):
        self._store = store

    def load(self) -> dict[str, int]:
        return self._store.load()

    def save(self, stocks: Mapping[str, int]) -> None:
        self._store.save(dict(stocks))

    def stock_of(self, item: str) -> int:
        return int(self.load().get(item, 0))


def initial_stocks(item_names: Iterable[str], per_item: int = 20) -> dict[str, int]:
    return {name: per_item for name in item_names}

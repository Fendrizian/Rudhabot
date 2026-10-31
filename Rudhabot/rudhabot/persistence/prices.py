"""Crypto price persistence (crypto.json)."""
from __future__ import annotations

from typing import Iterable, Mapping

from rudhabot.persistence.json_store import JsonStore

STARTING_PRICE = 100


class PriceBook:
    def __init__(self, store: JsonStore):
        self._store = store

    def load(self) -> dict[str, int]:
        return {coin: int(round(price)) for coin, price in self._store.load().items()}

    def save(self, prices: Mapping[str, float]) -> None:
        self._store.save({coin: int(round(price)) for coin, price in prices.items()})


def starting_prices(coin_symbols: Iterable[str]) -> dict[str, int]:
    return {coin: STARTING_PRICE for coin in coin_symbols}

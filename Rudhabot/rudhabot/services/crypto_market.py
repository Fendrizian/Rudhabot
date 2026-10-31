"""The crypto market: live prices, the periodic price walk, and buying/selling against accounts."""
from __future__ import annotations

import random
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from rudhabot.domain.crypto import CoinSpec
from rudhabot.persistence.prices import PriceBook
from rudhabot.persistence.users import UserRepository


class TradeRefused(Exception):
    def __init__(self, code: str, **data):
        super().__init__(code)
        self.code = code
        self.data = data


@dataclass(frozen=True)
class Trade:
    coin: str
    quantity: float
    value: float          # wallet money spent (buy) or received (sell)


@dataclass(frozen=True)
class Holding:
    coin: str
    name: str
    quantity: float
    value: float


class CryptoMarket:
    PRICE_FLOOR = 100

    def __init__(self, prices: PriceBook, coins: Sequence[CoinSpec], users: UserRepository,
                 rng: random.Random = random):
        self._book = prices
        self._rng = rng
        self._users = users
        self.coins: Mapping[str, CoinSpec] = {c.symbol: c for c in coins}
        self._prices: dict[str, int] = self._book.load()
        for symbol in self.coins:                    # a coin added later starts at the floor price
            self._prices.setdefault(symbol, self.PRICE_FLOOR)

    # ------------------------------------------------------------------ prices
    @property
    def prices(self) -> Mapping[str, int]:
        """Read-only live view of current prices."""
        return MappingProxyType(self._prices)

    def price(self, coin: str) -> int:
        return self._prices[coin]

    def tick(self) -> None:
        """One step of the random walk (same formula and draw order as the original task), then persist."""
        for symbol, spec in self.coins.items():
            change = 1 + self._rng.uniform(-spec.volatility, spec.volatility)
            self._prices[symbol] = int(round(self._prices[symbol] * change))
            if self._prices[symbol] < self.PRICE_FLOOR:
                self._prices[symbol] = self.PRICE_FLOOR
        self._book.save(self._prices)

    # ----------------------------------------------------------------- trading
    def buy(self, user_id: int | str, coin: str, amount: int) -> Trade:
        coin = coin.upper()
        if coin not in self.coins:
            raise TradeRefused("invalid_coin")
        if amount <= 0:                                  # a negative amount used to *credit* the wallet
            raise TradeRefused("not_positive")
        self._users.get(user_id)                         # make sure the account exists / is normalised
        with self._users.transaction() as accounts:
            account = accounts[str(user_id)]
            if amount > round(float(account["wallet"]), 2):
                raise TradeRefused("insufficient_wallet")
            bought = round(amount / round(float(self._prices[coin]), 2), 4)
            account["wallet"] = int(round(float(account["wallet"]) - amount, 2))
            account["crypto"][coin] = round(float(account["crypto"].get(coin, 0.0)) + bought, 4)
        return Trade(coin, bought, amount)

    def sell(self, user_id: int | str, coin: str, amount_text: str) -> Trade:
        coin = coin.upper()
        if coin not in self.coins:
            raise TradeRefused("invalid_coin")
        self._users.get(user_id)
        with self._users.transaction() as accounts:
            account = accounts[str(user_id)]
            owned = round(float(account["crypto"].get(coin, 0.0)), 4)
            if amount_text.lower() == "all":
                quantity = owned
            else:
                try:
                    quantity = round(float(amount_text), 4)
                except ValueError:
                    raise TradeRefused("invalid_amount") from None
            if quantity <= 0:
                raise TradeRefused("not_positive")
            if quantity > owned:
                raise TradeRefused("insufficient_coins", coin=coin)
            value = round(quantity * round(float(self._prices[coin]), 2), 2)
            account["wallet"] = int(round(float(account["wallet"]) + value, 2))
            account["crypto"][coin] = round(owned - quantity, 4)
        return Trade(coin, quantity, value)

    def portfolio(self, account: dict) -> list[Holding]:
        holdings = []
        for coin, amount in account.get("crypto", {}).items():
            if amount <= 0:
                continue
            quantity = round(float(amount), 4)
            price = round(int(self._prices.get(coin, 0)), 2)
            holdings.append(Holding(coin, self.coins[coin].name, quantity, round(int(quantity * price), 2)))
        return holdings

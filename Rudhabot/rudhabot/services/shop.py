"""The cafe menu (monthly specials, stock) and the rules for buying and selling items."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rudhabot.clock import Clock
from rudhabot.domain.catalog import ItemCatalog
from rudhabot.persistence.specials import SpecialStockRepository
from rudhabot.persistence.users import UserRepository

SPECIAL_STOCK = 20          # starting (and displayed maximum) stock of every monthly special
SELL_RATIO = 0.5


class ShopRefused(Exception):
    def __init__(self, code: str, **data):
        super().__init__(code)
        self.code = code
        self.data = data


@dataclass(frozen=True)
class Purchase:
    item: str
    quantity: int
    total: int
    special: bool


@dataclass(frozen=True)
class Sale:
    item: str
    quantity: int
    total: int


class CafeMenu:
    """What the Joot Cafe serves right now. All calendar-dependent answers come from the injected clock."""

    def __init__(self, catalog: ItemCatalog, stocks: SpecialStockRepository, clock: Clock):
        self._catalog = catalog
        self._stocks = stocks
        self._clock = clock

    @property
    def month(self) -> int:
        return self._clock.now().month

    @property
    def month_name(self) -> str:
        return self._clock.now().strftime("%B")

    @property
    def stock_cap(self) -> int:
        return SPECIAL_STOCK

    def special_items(self) -> dict[str, int]:
        return self._catalog.special_items(self.month)

    def specials_with_stock(self) -> list[tuple[str, int, int]]:
        stocks = self._stocks.load()
        return [(item, price, stocks.get(item, 0)) for item, price in self.special_items().items()]

    def all_items(self) -> dict[str, int]:
        return self._catalog.cafe_items(self.month)


class ShopService:
    """`bank` enables paying by credit card; `exchange` turns every purchase into revenue for the listed company
    (shop -> NAS, café -> JOOT), which is what moves their share prices."""

    def __init__(self, catalog: ItemCatalog, menu: CafeMenu, users: UserRepository,
                 stocks: SpecialStockRepository, bank=None, exchange=None):
        self._catalog = catalog
        self._menu = menu
        self._users = users
        self._stocks = stocks
        self._bank = bank
        self._exchange = exchange

    def company_of(self, item: str) -> str:
        return "NAS" if item in self._catalog.shop_items else "JOOT"

    def price_of(self, item: str) -> Optional[int]:
        """Shop price first, then the cafe (including this month's specials)."""
        if item in self._catalog.shop_items:
            return self._catalog.shop_items[item]
        return self._menu.all_items().get(item)

    def buy(self, user_id: int | str, item: str, quantity: int, pay_with: str = "wallet") -> Purchase:
        """Buy `quantity` of a canonical item name, paid from the wallet or by credit card.
        Raises ShopRefused(code) when a rule says no."""
        if quantity < 1:
            raise ShopRefused("not_positive")
        price = self.price_of(item)
        if price is None:
            raise ShopRefused("unknown_item")
        month = self._menu.month
        special = item in self._menu.special_items()
        if special:
            if self._users.has_purchased_special(user_id, item, month):
                raise ShopRefused("already_bought_special")
            if quantity > 1:
                raise ShopRefused("special_quantity")
            if self._stocks.stock_of(item) <= 0:
                raise ShopRefused("sold_out")
        total = price * quantity
        account = self._users.get(user_id)
        inventory = account.get("inventory", {})
        inventory[item] = inventory.get(item, 0) + quantity
        if pay_with == "card":
            if self._bank is None:
                raise ShopRefused("card_declined", message="❌ Card payments are unavailable.")
            from rudhabot.services.bank import BankRefused
            try:
                self._bank.charge(user_id, total, f"{quantity} × {item}")
            except BankRefused as refusal:
                raise ShopRefused("card_declined", message=str(refusal)) from None
            self._users.update(user_id, inventory=inventory)
        else:
            if account["wallet"] < total:
                raise ShopRefused("insufficient_wallet", quantity=quantity)
            self._users.update(user_id, wallet=account["wallet"] - total, inventory=inventory)
        if special:
            stocks = self._stocks.load()
            stocks[item] = max(0, stocks.get(item, 0) - 1)
            self._stocks.save(stocks)
            self._users.record_special_purchase(user_id, item, month)
        if self._exchange is not None:
            self._exchange.record_revenue(self.company_of(item), total, user_id)
        return Purchase(item, quantity, total, special)

    def sell(self, user_id: int | str, item: str, quantity: int) -> Sale:
        if item not in self._catalog.shop_items:
            raise ShopRefused("cannot_sell")
        account = self._users.get(user_id)
        inventory = account.get("inventory", {})
        if item not in inventory or inventory[item] < quantity or quantity < 1:
            raise ShopRefused("not_enough_items")
        total = int(self._catalog.shop_items[item] * SELL_RATIO) * quantity
        inventory[item] -= quantity
        if inventory[item] <= 0:
            del inventory[item]
        self._users.update(user_id, inventory=inventory, wallet=account["wallet"] + total)
        if self._exchange is not None:
            self._exchange.record_revenue("NAS", -total)        # the shop buying stock back is an expense
        return Sale(item, quantity, total)

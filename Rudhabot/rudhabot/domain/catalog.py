"""Item catalog: prices, emojis, descriptions, aliases and the monthly cafe specials."""
from __future__ import annotations

from typing import Mapping, Optional

from rudhabot import catalog_data as data

#: Items outside the monthly rotation that are always in the specials stock list.
_ALWAYS_SPECIAL = "pog juice"


class ItemCatalog:
    """Read-only view over the item tables. Methods that depend on the calendar take the month explicitly,
    so the catalog itself never touches the clock."""

    def __init__(
        self,
        *,
        shop_items: Mapping[str, int] = data.shop_items,
        specialty_drink: Mapping[str, int] = data.specialty_drink,
        cafe_food_items: Mapping[str, int] = data.cafe_food_items,
        cafe_beverage_items: Mapping[str, int] = data.cafe_beverage_items,
        monthly_specials: Mapping[int, Mapping[str, Mapping[str, int]]] = data.monthly_specials,
        item_emojis: Mapping[str, str] = data.item_emojis,
        item_descriptions: Mapping[str, str] = data.item_descriptions,
        item_aliases: Mapping[str, list] = data.item_aliases,
    ):
        self.shop_items = dict(shop_items)
        self.specialty_drink = dict(specialty_drink)
        self.cafe_food_items = dict(cafe_food_items)
        self.cafe_beverage_items = dict(cafe_beverage_items)
        self.monthly_specials = monthly_specials
        self._emojis = dict(item_emojis)
        self._descriptions = dict(item_descriptions)
        self._aliases = dict(item_aliases)

    # ------------------------------------------------------------ per-item data
    def emoji(self, item: str, default: str = "") -> str:
        return self._emojis.get(item, default)

    def description(self, item: str, default: str = "") -> str:
        return self._descriptions.get(item, default)

    def shop_price(self, item: str, default: Optional[int] = None) -> Optional[int]:
        return self.shop_items.get(item, default)

    def resolve(self, name: str) -> Optional[str]:
        """Map a user-typed name or alias to the canonical item name (None if unknown)."""
        name = name.lower().strip()
        for canonical, aliases in self._aliases.items():
            if name in aliases:
                return canonical
        return None

    # ------------------------------------------------------------- cafe / specials
    def specials_for(self, month: int) -> Mapping[str, Mapping[str, int]]:
        return self.monthly_specials.get(month, self.monthly_specials[1])

    def special_items(self, month: int) -> dict[str, int]:
        monthly = self.specials_for(month)
        items: dict[str, int] = {}
        items.update(self.specialty_drink)
        items.update(monthly["food"])
        items.update(monthly["beverage"])
        return items

    def cafe_items(self, month: int) -> dict[str, int]:
        """Regular cafe menu plus this month's specials."""
        items: dict[str, int] = {}
        items.update(self.cafe_food_items)
        items.update(self.cafe_beverage_items)
        items.update(self.special_items(month))
        return items

    def all_cafe_item_names(self) -> set[str]:
        """Every cafe item that exists in any month (what the cafe can ever serve)."""
        names = set(self.cafe_food_items) | set(self.cafe_beverage_items) | set(self.specialty_drink)
        for month in self.monthly_specials.values():
            names.update(month["food"])
            names.update(month["beverage"])
        return names

    def is_beverage(self, item: str) -> bool:
        return (
            item in self.cafe_beverage_items
            or item in self.specialty_drink
            or any(item in month["beverage"] for month in self.monthly_specials.values())
        )

    def special_item_names(self) -> list[str]:
        """Items that need a stock counter: every monthly special plus pog juice."""
        names: list[str] = []
        for month in self.monthly_specials.values():
            names.extend(month["food"])
            names.extend(month["beverage"])
        names.append(_ALWAYS_SPECIAL)
        return names

    # ------------------------------------------------------------------ mystery box
    def mystery_box_pool(self) -> dict[str, int]:
        shop_prizes = {k: v for k, v in self.shop_items.items() if k != "certificate of rich"}
        return {**shop_prizes, **self.cafe_food_items, **self.cafe_beverage_items}

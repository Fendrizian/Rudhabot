"""Item catalog: prices, emojis, descriptions, aliases and the monthly cafe specials."""
from __future__ import annotations

import re
import unicodedata
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
        item_lore: Mapping[str, str] = data.item_lore,
    ):
        self.shop_items = dict(shop_items)
        self.specialty_drink = dict(specialty_drink)
        self.cafe_food_items = dict(cafe_food_items)
        self.cafe_beverage_items = dict(cafe_beverage_items)
        self.monthly_specials = monthly_specials
        self._emojis = dict(item_emojis)
        self._descriptions = dict(item_descriptions)
        self._aliases = {item: [a for a in names if normalize(a) != normalize(item)] for item, names in item_aliases.items()}
        self._lore = dict(item_lore)
        self._index: dict[str, str] = {}        # normalized name or alias -> item; full names win over aliases
        for item in self.all_items():
            self._index[normalize(item)] = item
        for item, names in self._aliases.items():
            for alias in names:
                self._index.setdefault(normalize(alias), item)

    # ------------------------------------------------------------ per-item data
    def emoji(self, item: str, default: str = "") -> str:
        return self._emojis.get(item, default)

    def description(self, item: str, default: str = "") -> str:
        return self._descriptions.get(item, default)

    def shop_price(self, item: str, default: Optional[int] = None) -> Optional[int]:
        return self.shop_items.get(item, default)

    def lore(self, item: str) -> str:
        return self._lore.get(item, "")

    def aliases(self, item: str) -> list[str]:
        """The extra names an item answers to (its full name not included)."""
        return list(self._aliases.get(item, ()))

    def resolve(self, name: str) -> Optional[str]:
        """Map a user-typed name or alias to the canonical item name (None if unknown).

        Case, accents, hyphens and extra spaces are ignored, and a plural "s" may be added or dropped."""
        key = normalize(name)
        if not key:
            return None
        for candidate in (key, key[:-1] if key.endswith("s") else key + "s"):
            if candidate in self._index:
                return self._index[candidate]
        return None

    def all_items(self) -> list[str]:
        """Every real item: the shop plus everything the cafe can ever serve."""
        return list(self.shop_items) + sorted(self.all_cafe_item_names() - set(self.shop_items))

    def search(self, query: str, limit: int = 25) -> list[str]:
        """Items whose name or an alias contains `query` (for autocomplete); exact and prefix matches first."""
        q = normalize(query)
        def rank(item):
            names = [normalize(item)] + [normalize(a) for a in self._aliases.get(item, ())]
            if q in names:
                return 0
            if any(n.startswith(q) for n in names):
                return 1
            return 2 if any(q in n for n in names) else None
        ranked = [(r, item) for item in self.all_items() if (r := rank(item)) is not None]
        return [item for _, item in sorted(ranked, key=lambda t: (t[0], t[1]))][:limit]

    def kind(self, item: str) -> str:
        if item in self.shop_items:
            return "Shop item"
        if item in self.specialty_drink:
            return "Café specialty"
        for month in self.monthly_specials.values():
            if item in month["food"] or item in month["beverage"]:
                return "Café monthly special"
        return "Café menu"

    def cafe_price(self, item: str) -> Optional[int]:
        """The item's café price in whichever month it is served."""
        for table in (self.cafe_food_items, self.cafe_beverage_items, self.specialty_drink):
            if item in table:
                return table[item]
        for month in self.monthly_specials.values():
            for table in (month["food"], month["beverage"]):
                if item in table:
                    return table[item]
        return None

    def special_months(self, item: str) -> list[int]:
        return [m for m, month in sorted(self.monthly_specials.items()) if item in month["food"] or item in month["beverage"]]

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


def normalize(name: str) -> str:
    """Lower-case, strip accents, treat hyphens/underscores as spaces and collapse whitespace."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[-_]", " ", text)).strip()

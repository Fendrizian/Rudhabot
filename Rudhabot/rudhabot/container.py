"""The composition root's dependency graph: builds every repository and service once and hands them out."""
from __future__ import annotations

import random
from dataclasses import dataclass

import discord

from rudhabot.clock import Clock, SystemClock
from rudhabot.domain.catalog import ItemCatalog
from rudhabot.domain.crypto import DEFAULT_COINS
from rudhabot.domain.lottery import Lottery
from rudhabot.domain.mysterybox import MysteryBox
from rudhabot.domain.slots import SlotMachine
from rudhabot.persistence.birthdays import BirthdayRegistry, CelebrationLog
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.lottery import LotteryLedger, empty_ledger
from rudhabot.persistence.prices import PriceBook, starting_prices
from rudhabot.persistence.specials import SpecialStockRepository, initial_stocks
from rudhabot.persistence.users import UserRepository
from rudhabot.services.birthdays import BirthdayService
from rudhabot.services.crypto_market import CryptoMarket
from rudhabot.services.loans import LoanService
from rudhabot.services.lottery import LotteryService
from rudhabot.services.permissions import AdminPolicy
from rudhabot.services.sessions import GamblingSessions
from rudhabot.services.shop import CafeMenu, ShopService
from rudhabot.settings import Settings
from rudhabot.ui.announcements import Announcer
from rudhabot.ui.loan_notices import LoanNotifier
from rudhabot.ui.messenger import Messenger


@dataclass(frozen=True)
class Services:
    settings: Settings
    clock: Clock
    rng: random.Random
    catalog: ItemCatalog
    users: UserRepository
    menu: CafeMenu
    shop: ShopService
    sessions: GamblingSessions
    admin: AdminPolicy
    loans: LoanService
    crypto: CryptoMarket
    lottery: LotteryService
    birthdays: BirthdayService
    slots: SlotMachine
    mystery_box: MysteryBox
    messenger: Messenger
    loan_notices: LoanNotifier
    announcer: Announcer

    @classmethod
    def build(cls, settings: Settings, client: discord.Client, *, clock: Clock | None = None,
              rng: random.Random = random) -> "Services":
        """Wire everything. `rng` is injectable so a whole bot can be replayed deterministically in tests."""
        clock = clock or SystemClock()
        paths = settings.paths
        coins = DEFAULT_COINS
        symbols = [c.symbol for c in coins]
        catalog = ItemCatalog()

        users = UserRepository(JsonStore(paths.data, dict), symbols)
        stocks = SpecialStockRepository(JsonStore(paths.specials, lambda: initial_stocks(catalog.special_item_names(), 20)))
        menu = CafeMenu(catalog, stocks, clock)
        messenger = Messenger(client)

        return cls(
            settings=settings,
            clock=clock,
            rng=rng,
            catalog=catalog,
            users=users,
            menu=menu,
            shop=ShopService(catalog, menu, users, stocks),
            sessions=GamblingSessions(),
            admin=AdminPolicy(settings.admin_role_id),
            loans=LoanService(users, settings.loans, clock),
            crypto=CryptoMarket(PriceBook(JsonStore(paths.crypto, lambda: starting_prices(symbols))), coins, users, rng),
            lottery=LotteryService(users, LotteryLedger(JsonStore(paths.lottery, empty_ledger)), Lottery(rng=rng), clock),
            birthdays=BirthdayService(BirthdayRegistry(JsonStore(paths.birthday_registry, dict)),
                                      CelebrationLog(JsonStore(paths.birthday_log, list, indent=2)),
                                      users, settings.birthdays, clock),
            slots=SlotMachine(rng=rng),
            mystery_box=MysteryBox(catalog.mystery_box_pool(), rng=rng),
            messenger=messenger,
            loan_notices=LoanNotifier(messenger),
            announcer=Announcer(messenger, settings.channels, catalog),
        )

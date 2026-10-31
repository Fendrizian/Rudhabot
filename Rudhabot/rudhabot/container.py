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
from rudhabot.llm.assistant import Assistant
from rudhabot.llm.knowledge import KnowledgeBase
from rudhabot.llm.limits import RateLimiter
from rudhabot.persistence.birthdays import BirthdayRegistry, CelebrationLog
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.lottery import LotteryLedger, empty_ledger
from rudhabot.persistence.prices import PriceBook, starting_prices
from rudhabot.persistence.specials import SpecialStockRepository, initial_stocks
from rudhabot.persistence.tcg import TcgRepository, empty_matches
from rudhabot.persistence.users import UserRepository
from rudhabot.services.birthdays import BirthdayService
from rudhabot.services.crypto_market import CryptoMarket
from rudhabot.services.loans import LoanService
from rudhabot.services.oracle import OracleService
from rudhabot.services.lottery import LotteryService
from rudhabot.services.permissions import AdminPolicy
from rudhabot.services.sessions import GamblingSessions
from rudhabot.services.shop import CafeMenu, ShopService
from rudhabot.services.tcg import TcgService, TcgTerms
from rudhabot.settings import OPENAI_KEY_ENV_VAR, Settings
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
    oracle: OracleService
    tcg: TcgService
    assistant: Assistant

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
        ai = settings.ai
        oracle = OracleService(ai.models_dir, enabled=ai.oracle_enabled, version=ai.oracle_version,
                               rng=random.Random(rng.random()))
        tcg = TcgService(TcgRepository(JsonStore(paths.tcg, dict), JsonStore(paths.tcg_matches, empty_matches)),
                         users, clock, rng, TcgTerms(), oracle=oracle)

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
            oracle=oracle,
            tcg=tcg,
            assistant=_build_assistant(settings, tcg, oracle),
        )


def _build_assistant(settings: Settings, tcg: TcgService, oracle: OracleService) -> Assistant:
    """The LLM is optional: without OPENAI_API_KEY (or with RUDHABOT_LLM_ENABLED=0) /ask answers from local docs."""
    import os
    ai = settings.ai
    model = None
    key = os.environ.get(OPENAI_KEY_ENV_VAR, "").strip()
    if ai.llm_enabled and key:
        from rudhabot.llm.client import OpenAIResponsesModel
        model = OpenAIResponsesModel(key, ai.llm_model, timeout=ai.llm_timeout_s, reasoning_effort=ai.llm_reasoning_effort)
    limiter = RateLimiter(ai.llm_per_user_per_hour, ai.llm_per_guild_per_hour, ai.llm_daily_budget)
    return Assistant(KnowledgeBase(), model, limiter, ai.llm_max_output_tokens,
                     status_provider=lambda uid: tcg_status(tcg, uid), similar_cards=oracle.similar_cards)


def tcg_status(tcg: TcgService, user_id: int) -> dict:
    """The caller's own Cloudfall state for the assistant: no other user's data, no hidden match information."""
    from rudhabot.tcg import engine as E
    profile = tcg.profile(user_id)
    status = {"has_starter": bool(profile["starter"]), "active_deck": profile["active_deck"], "rating": profile["rating"],
              "record": f"{profile['wins']}W-{profile['losses']}L-{profile['draws']}D",
              "collection_size": sum(profile["collection"].values())}
    match = tcg.match_for(user_id)
    if match is not None:
        summary = E.public_summary(match.game)
        seat = match.seat_of(user_id)
        from rudhabot.tcg.cards import CARD_BY_ID
        summary["your_hand"] = [CARD_BY_ID[c].name for c in match.game.players[seat].hand]
        status["current_match"] = summary
    return status

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
from rudhabot.persistence.database import Storage
from rudhabot.persistence.lottery import LotteryLedger, empty_ledger
from rudhabot.persistence.prices import PriceBook, starting_prices
from rudhabot.persistence.specials import SpecialStockRepository, initial_stocks
from rudhabot.persistence.tcg import TcgRepository
from rudhabot.persistence.users import UserRepository
from rudhabot.ml.intent import MODEL_FILE as INTENT_MODEL_FILE, IntentRouter
from rudhabot.services.audit import AuditService
from rudhabot.services.bank import BankService
from rudhabot.services.birthdays import BirthdayService
from rudhabot.services.gacha import GachaService
from rudhabot.services.review import ReviewService
from rudhabot.services.security import SecurityService
from rudhabot.services.stocks import StockExchange, initial_state
from rudhabot.services.crypto_market import CryptoMarket
from rudhabot.services.loans import LoanService
from rudhabot.services.clairvoyance import ClairvoyanceService
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
    clairvoyance: ClairvoyanceService
    tcg: TcgService
    assistant: Assistant
    audit: AuditService
    bank: BankService
    stocks: StockExchange
    gacha: GachaService
    reviews: ReviewService
    intents: IntentRouter
    storage: Storage
    security: SecurityService

    @classmethod
    def build(cls, settings: Settings, client: discord.Client, *, clock: Clock | None = None,
              rng: random.Random = random) -> "Services":
        """Wire everything. `rng` is injectable so a whole bot can be replayed deterministically in tests."""
        clock = clock or SystemClock()
        paths = settings.paths
        coins = DEFAULT_COINS
        symbols = [c.symbol for c in coins]
        catalog = ItemCatalog()
        storage = Storage.open(settings.storage, paths.database)       # SQLite file, or the JSON files
        docs, value = storage.docs, storage.value

        # the audit trail comes first: every repository that holds money reports to it
        audit = AuditService(storage.ledger("economy_ledger", paths.ledger), clock, settings.channels.audit)
        reviews = ReviewService(value("reviews", paths.reviews, dict), audit.ledger,
                                storage.ledger("failures", paths.failures), clock)
        audit.listeners.append(reviews.on_audit)
        users = UserRepository(docs("accounts", paths.data), symbols, audit=audit)
        stocks = SpecialStockRepository(value("specials", paths.specials,
                                              lambda: initial_stocks(catalog.special_item_names(), 20)))
        menu = CafeMenu(catalog, stocks, clock)
        messenger = Messenger(client)
        ai = settings.ai
        clairvoyance = ClairvoyanceService(ai.models_dir, enabled=ai.clairvoyance_enabled, version=ai.clairvoyance_version,
                               rng=random.Random(rng.random()))
        tcg_repo = TcgRepository(docs("tcg_profiles", paths.tcg), docs("tcg_matches", paths.tcg_matches))
        tcg = TcgService(tcg_repo, users, clock, rng, TcgTerms.from_env(), clairvoyance=clairvoyance)
        bank = BankService(docs("bank", paths.bank), users, clock, audit)
        exchange = StockExchange(value("stocks", paths.stocks, lambda: initial_state(clock.now().isoformat(timespec="seconds"))),
                                 users, clock, random.Random(rng.random()), audit)
        shop = ShopService(catalog, menu, users, stocks, bank=bank, exchange=exchange)
        gacha = GachaService(tcg_repo, value("gacha", paths.gacha, dict), users, clock, rng, audit, bank)
        tcg.gacha = gacha
        tcg.bank = bank

        assistant = _build_assistant(settings, tcg, clairvoyance)
        return cls(
            settings=settings,
            clock=clock,
            rng=rng,
            catalog=catalog,
            users=users,
            menu=menu,
            shop=shop,
            sessions=GamblingSessions(),
            admin=AdminPolicy(settings.admin_role_id, settings.banker_role_id),
            loans=LoanService(users, settings.loans, clock),
            crypto=CryptoMarket(PriceBook(value("crypto_prices", paths.crypto, lambda: starting_prices(symbols))),
                                coins, users, rng),
            lottery=LotteryService(users, LotteryLedger(value("lottery", paths.lottery, empty_ledger)), Lottery(rng=rng),
                                   clock),
            birthdays=BirthdayService(BirthdayRegistry(value("birthday_registry", paths.birthday_registry, dict)),
                                      CelebrationLog(value("birthday_log", paths.birthday_log, list, indent=2)),
                                      users, settings.birthdays, clock),
            slots=SlotMachine(rng=rng),
            mystery_box=MysteryBox(catalog.mystery_box_pool(), rng=rng),
            messenger=messenger,
            loan_notices=LoanNotifier(messenger),
            announcer=Announcer(messenger, settings.channels, catalog),
            clairvoyance=clairvoyance,
            tcg=tcg,
            assistant=assistant,
            audit=audit,
            bank=bank,
            stocks=exchange,
            gacha=gacha,
            reviews=reviews,
            intents=IntentRouter(_intent_model(ai.models_dir)),
            storage=storage,
            security=_build_security(settings, storage, clock, client, assistant),
        )


def _build_security(settings: Settings, storage: Storage, clock: Clock, client: discord.Client,
                    assistant: Assistant) -> SecurityService:
    """Security keeps its records in SQLite even when the economy uses JSON files (RUDHABOT_STORAGE=json)."""
    import os
    from rudhabot.ml.security.serving import SecurityModels
    from rudhabot.persistence.database import Database
    from rudhabot.persistence.security import SecurityStore
    from rudhabot.security.briefing import Briefer
    from rudhabot.security.discord_port import DiscordGuildPort
    db = storage.db or Database(settings.paths.database)
    store = SecurityStore(db, clock)
    enabled = (os.environ.get("RUDHABOT_SECURITY_MODELS", "1").strip().lower() not in ("0", "false", "off", "no"))
    models = SecurityModels(os.path.join(settings.ai.models_dir, "security"), enabled=enabled)
    briefer = Briefer(assistant.model, assistant.limiter)
    automod_counts: dict[int, int] = {}
    role_tools = os.environ.get("RUDHABOT_ROLE_TOOLS", "1").strip().lower() not in ("0", "false", "off", "no")
    return SecurityService(store, clock, admin_role_id=settings.admin_role_id, models=models, briefer=briefer,
                           port_factory=lambda gid: DiscordGuildPort(client, gid, lambda: automod_counts.get(gid, 0)),
                           bot_id=lambda: client.user.id if client.user else 0, role_tools=role_tools)


def _intent_model(models_dir: str):
    import os
    path = os.path.join(models_dir, INTENT_MODEL_FILE)
    return path if os.path.exists(path) else None


def _build_assistant(settings: Settings, tcg: TcgService, clairvoyance: ClairvoyanceService) -> Assistant:
    """The LLM is optional: without OPENAI_API_KEY (or with RUDHABOT_LLM_ENABLED=0) /ask answers from local docs."""
    import os
    import logging
    log = logging.getLogger("rudhabot.llm")
    ai = settings.ai
    model, reason = None, None
    key = os.environ.get(OPENAI_KEY_ENV_VAR, "").strip()
    if not ai.llm_enabled:
        reason = "RUDHABOT_LLM_ENABLED=0"
    elif not key:
        reason = f"{OPENAI_KEY_ENV_VAR} is not set (export it, or put it in the .env file next to main.py)"
    else:
        try:
            from rudhabot.llm.client import OpenAIResponsesModel
            model = OpenAIResponsesModel(key, ai.llm_model, timeout=ai.llm_timeout_s,
                                         reasoning_effort=ai.llm_reasoning_effort)
        except ImportError:
            reason = "the `openai` package is not installed (pip install -r requirements.txt)"
    if model is not None:
        log.info("OpenAI assistant enabled: model %s%s", ai.llm_model,
                 f", reasoning effort {model.reasoning_effort}" if model.reasoning_effort else "")
    else:
        log.warning("OpenAI assistant disabled: %s. /ask will answer from local docs only.", reason)
    limiter = RateLimiter(ai.llm_per_user_per_hour, ai.llm_per_guild_per_hour, ai.llm_daily_budget)
    return Assistant(KnowledgeBase(), model, limiter, ai.llm_max_output_tokens,
                     status_provider=lambda uid: tcg_status(tcg, uid), similar_cards=clairvoyance.similar_cards,
                     disabled_reason=reason)


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

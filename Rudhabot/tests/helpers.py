"""Shared test fixtures: a throw-away data directory and ready-wired repositories."""
import tempfile
import unittest
from datetime import datetime

from rudhabot.clock import FixedClock
from rudhabot.domain.catalog import ItemCatalog
from rudhabot.domain.crypto import DEFAULT_COINS
from rudhabot.persistence.birthdays import BirthdayRegistry, CelebrationLog
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.lottery import LotteryLedger, empty_ledger
from rudhabot.persistence.prices import PriceBook, starting_prices
from rudhabot.persistence.specials import SpecialStockRepository
from rudhabot.persistence.users import UserRepository
from rudhabot.settings import Paths, Settings

NOW = datetime(2026, 3, 15, 12, 0, 0)


class Env:
    """All repositories over a fresh temporary directory."""

    def __init__(self, directory: str, now: datetime = NOW):
        self.directory = directory
        self.settings = Settings(paths=Paths.in_directory(directory))
        self.clock = FixedClock(now)
        self.catalog = ItemCatalog()
        symbols = [c.symbol for c in DEFAULT_COINS]
        p = self.settings.paths
        self.users = UserRepository(JsonStore(p.data, dict), symbols)
        self.prices = PriceBook(JsonStore(p.crypto, lambda: starting_prices(symbols)))
        self.stocks = SpecialStockRepository(JsonStore(p.specials, dict))
        self.ledger = LotteryLedger(JsonStore(p.lottery, empty_ledger))
        self.registry = BirthdayRegistry(JsonStore(p.birthday_registry, dict))
        self.celebrations = CelebrationLog(JsonStore(p.birthday_log, list, indent=2))


class EnvTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = Env(self._tmp.name)

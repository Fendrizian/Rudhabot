import random
from datetime import datetime, timedelta

from rudhabot.domain.birthday import BirthdayDate, BirthdayError
from rudhabot.domain.crypto import DEFAULT_COINS
from rudhabot.domain.loan import Loan
from rudhabot.services.birthdays import BirthdayService, CAKE_ITEM
from rudhabot.services.crypto_market import CryptoMarket, TradeRefused
from rudhabot.services.loans import AutoPaid, Bankruptcy, LoanRefused, LoanService
from rudhabot.services.sessions import GamblingSessions
from rudhabot.errors import AlreadyInGame
from rudhabot.services.permissions import AdminPolicy
from tests.helpers import EnvTestCase


class LoanServiceTests(EnvTestCase):
    def setUp(self):
        super().setUp()
        self.loans = LoanService(self.env.users, self.env.settings.loans, self.env.clock)

    def test_open_pays_out_and_stores_the_loan(self):
        self.env.users.update(1, wallet=50)
        opened = self.loans.open(1, 500, 5)
        account = self.env.users.get(1)
        self.assertEqual(account["wallet"], 550)
        self.assertEqual(account["loan"]["principal"], 500)
        self.assertEqual(opened.due, self.env.clock.now() + timedelta(days=5))
        self.assertFalse(account["loan_warning_sent"])

    def test_open_refusals(self):
        for amount, days in [(50, 5), (20000, 5), (500, 0), (500, 31)]:
            with self.assertRaises(LoanRefused) as ctx:
                self.loans.open(1, amount, days)
            self.assertEqual(ctx.exception.code, "invalid_terms")
        self.loans.open(1, 500, 5)
        with self.assertRaises(LoanRefused) as ctx:
            self.loans.open(1, 500, 5)
        self.assertEqual(ctx.exception.code, "already_has_loan")

    def test_ban_blocks_then_expires(self):
        self.env.users.update(1, loan_ban_until=(self.env.clock.now() + timedelta(days=3)).isoformat())
        with self.assertRaises(LoanRefused) as ctx:
            self.loans.open(1, 500, 5)
        self.assertEqual(ctx.exception.code, "banned")
        self.assertEqual(ctx.exception.data["days_remaining"], 4)     # legacy rounding: whole days elapsed + 1
        self.env.clock.advance(days=2, hours=12)
        with self.assertRaises(LoanRefused) as ctx:
            self.loans.open(1, 500, 5)
        self.assertEqual(ctx.exception.data["days_remaining"], 1)     # 12h left
        self.env.clock.advance(days=2)
        self.loans.open(1, 500, 5)                                    # ban expired -> allowed
        self.assertIsNone(self.env.users.get(1)["loan_ban_until"])

    def test_loan_survives_unrelated_account_updates(self):
        """Regression: the legacy update function cancelled the loan on ANY other update."""
        self.loans.open(1, 500, 5)
        self.env.users.update(1, wallet=self.env.users.get(1)["wallet"] + 20)      # e.g. /work
        self.assertIsNotNone(self.loans.active_loan(1))

    def test_extend(self):
        self.loans.open(1, 500, 5)
        self.env.users.update(1, wallet=100)
        result = self.loans.extend(1, 2)
        self.assertEqual((result.days, result.fee), (2, 50))
        self.assertEqual(self.env.users.get(1)["wallet"], 50)
        self.assertEqual(self.loans.active_loan(1).interest_days, 7)
        with self.assertRaises(LoanRefused) as ctx:
            self.loans.extend(1, 1)
        self.assertEqual(ctx.exception.code, "already_extended")

    def test_extend_refusals(self):
        with self.assertRaises(LoanRefused) as ctx:
            self.loans.extend(1, 1)
        self.assertEqual(ctx.exception.code, "no_loan_to_extend")
        self.loans.open(1, 500, 29)
        with self.assertRaises(LoanRefused) as ctx:
            self.loans.extend(1, 5)
        self.assertEqual((ctx.exception.code, ctx.exception.data["allowed"]), ("exceeds_limit", 1))

    def test_extend_needs_the_fee_in_the_wallet(self):
        self.loans.open(1, 500, 5)
        self.env.users.update(1, wallet=49)                           # fee is 10% of 500 = 50
        with self.assertRaises(LoanRefused) as ctx:
            self.loans.extend(1, 1)
        self.assertEqual((ctx.exception.code, ctx.exception.data["fee"]), ("cannot_afford_fee", 50))
        self.assertEqual(self.loans.active_loan(1).interest_days, 5)  # nothing changed

    def test_cannot_extend_a_loan_already_at_the_maximum(self):
        self.loans.open(1, 500, 30)
        with self.assertRaises(LoanRefused) as ctx:
            self.loans.extend(1, 1)
        self.assertEqual(ctx.exception.code, "at_max_days")

    def test_repay_partial_then_full_and_overpayment_is_capped(self):
        self.loans.open(1, 500, 5)
        part = self.loans.repay(1, "100")
        self.assertEqual((part.paid, part.remaining, part.cleared), (100, 400, False))
        self.env.users.update(1, wallet=10_000)
        done = self.loans.repay(1, "9999")
        self.assertTrue(done.cleared)
        self.assertEqual(done.paid, 400)                              # charged only what was owed
        self.assertEqual(self.env.users.get(1)["wallet"], 9_600)
        self.assertIsNone(self.loans.active_loan(1))

    def test_repay_refusals(self):
        with self.assertRaises(LoanRefused) as ctx:
            self.loans.repay(1, "5")
        self.assertEqual(ctx.exception.code, "no_loan_to_pay")
        self.loans.open(1, 500, 5)
        for text, code in [("abc", "invalid_amount"), ("0", "not_positive"), ("-5", "not_positive"), ("99999", "insufficient_wallet")]:
            with self.assertRaises(LoanRefused) as ctx:
                self.loans.repay(1, text)
            self.assertEqual(ctx.exception.code, code, text)

    def test_settlement_autopays_from_wallet_then_bank(self):
        self.loans.open(1, 1000, 2)
        self.env.users.update(1, wallet=300, bank=5000)
        self.env.clock.advance(days=3)
        events = self.loans.settle_due_loans()
        total = Loan.from_dict({"principal": 1000, "interest_rate": 0.02, "start_date": "x"} , 0.02) and round(1000 * 1.02 ** 2)
        self.assertEqual(events, [AutoPaid(1, total)])
        account = self.env.users.get(1)
        self.assertEqual((account["wallet"], account["bank"]), (0, 5000 - (total - 300)))
        self.assertIsNone(account["loan"])

    def test_settlement_bankruptcy(self):
        self.loans.open(1, 1000, 2)
        self.env.users.update(1, wallet=10, bank=10)
        self.env.clock.advance(days=3)
        (event,) = self.loans.settle_due_loans()
        self.assertIsInstance(event, Bankruptcy)
        self.assertEqual(event.ban_days, max(1, round(event.total_due / 200)))
        account = self.env.users.get(1)
        self.assertEqual((account["wallet"], account["bank"], account["loan"]), (0, 0, None))
        self.assertEqual(account["loan_ban_until"], event.ban_until.isoformat())

    def test_settlement_ignores_loans_not_yet_due(self):
        self.loans.open(1, 1000, 5)
        self.assertEqual(self.loans.settle_due_loans(), [])
        self.assertIsNotNone(self.loans.active_loan(1))

    def test_warnings_only_inside_the_window_and_only_once(self):
        self.loans.open(1, 1000, 3)
        self.assertEqual(self.loans.pending_warnings(), [])
        self.env.clock.advance(days=2, hours=1)                       # due in 23h -> inside 12h..36h
        (warning,) = self.loans.pending_warnings()
        self.assertEqual(warning.user_id, 1)
        self.loans.mark_warned(1)
        self.assertEqual(self.loans.pending_warnings(), [])


class CryptoMarketTests(EnvTestCase):
    def market(self, seed=1):
        return CryptoMarket(self.env.prices, DEFAULT_COINS, self.env.users, rng=random.Random(seed))

    def test_tick_matches_the_original_price_walk(self):
        rng, legacy = random.Random(5), {c.symbol: 100 for c in DEFAULT_COINS}
        market = CryptoMarket(self.env.prices, DEFAULT_COINS, self.env.users, rng=random.Random(5))
        for _ in range(200):
            market.tick()
            for c in DEFAULT_COINS:                                   # the original task, verbatim
                change = 1 + rng.uniform(-c.volatility, c.volatility)
                legacy[c.symbol] = int(round(legacy[c.symbol] * change))
                if legacy[c.symbol] < 100:
                    legacy[c.symbol] = 100
            self.assertEqual(dict(market.prices), legacy)
        self.assertEqual(self.env.prices.load(), legacy)              # and it is persisted

    def test_buy_then_sell_round_trip(self):
        market = self.market()
        self.env.users.update(1, wallet=1000)
        trade = market.buy(1, "scay", 500)
        self.assertEqual((trade.coin, trade.quantity), ("SCAY", 5.0))     # price 100
        account = self.env.users.get(1)
        self.assertEqual((account["wallet"], account["crypto"]["SCAY"]), (500, 5.0))
        sold = market.sell(1, "SCAY", "all")
        self.assertEqual((sold.quantity, sold.value), (5.0, 500.0))
        self.assertEqual(self.env.users.get(1)["wallet"], 1000)

    def test_negative_or_zero_purchases_are_refused(self):
        """Regression: a negative amount used to credit the wallet with free money."""
        market = self.market()
        self.env.users.update(1, wallet=100)
        for amount in (-1_000_000, 0):
            with self.assertRaises(TradeRefused) as ctx:
                market.buy(1, "SCAY", amount)
            self.assertEqual(ctx.exception.code, "not_positive")
        self.assertEqual(self.env.users.get(1)["wallet"], 100)

    def test_trade_refusals_change_nothing(self):
        market = self.market()
        self.env.users.update(1, wallet=100)
        cases = [("buy", ("XXX", 10), "invalid_coin"), ("buy", ("SCAY", 101), "insufficient_wallet"),
                 ("sell", ("SCAY", "all"), "not_positive"), ("sell", ("SCAY", "1"), "insufficient_coins"),
                 ("sell", ("SCAY", "abc"), "invalid_amount"), ("sell", ("ZZZ", "1"), "invalid_coin")]
        for method, args, code in cases:
            with self.assertRaises(TradeRefused) as ctx:
                getattr(market, method)(1, *args)
            self.assertEqual(ctx.exception.code, code, (method, args))
        self.assertEqual(self.env.users.get(1)["wallet"], 100)


class BirthdayServiceTests(EnvTestCase):
    def setUp(self):
        super().setUp()
        # 2026-03-15 12:00 UTC == 20:00 in GMT+8
        self.svc = BirthdayService(self.env.registry, self.env.celebrations, self.env.users,
                                   self.env.settings.birthdays, self.env.clock)

    def test_register_view_remove(self):
        self.svc.set_birthday(1, 3, 15)
        self.assertEqual(self.svc.get_birthday(1), BirthdayDate(3, 15))
        self.assertTrue(self.svc.remove_birthday(1))
        self.assertFalse(self.svc.remove_birthday(1))
        self.assertIsNone(self.svc.get_birthday(1))

    def test_validation(self):
        for args in [(13, 1), (2, 30), (3, 15, 1800), (3, 15, 2999), (12, 31, 2026)]:
            with self.assertRaises(BirthdayError, msg=str(args)):
                self.svc.set_birthday(1, *args)

    def test_greeting_pays_once_per_year(self):
        self.svc.set_birthday(1, 3, 15, 2000)
        self.svc.set_birthday(2, 3, 16)
        first = self.svc.collect_due_greetings()
        self.assertEqual([(g.user_id, g.age, g.gift) for g in first], [(1, 26, 500)])
        self.assertEqual(self.env.users.get(1)["wallet"], 500)
        self.assertEqual(self.svc.collect_due_greetings(), [])         # polling again must not pay again
        self.assertEqual(self.env.users.get(1)["wallet"], 500)
        self.env.clock.advance(days=1)
        self.assertEqual([g.user_id for g in self.svc.collect_due_greetings()], [2])

    def test_changing_the_date_does_not_retrigger_this_years_gift(self):
        self.svc.set_birthday(1, 3, 15)
        self.svc.collect_due_greetings()
        self.svc.set_birthday(1, 3, 15, 1999)
        self.assertEqual(self.svc.collect_due_greetings(), [])

    def test_uses_the_gmt_plus_8_calendar_day(self):
        self.env.clock.set(datetime(2026, 3, 15, 17, 0))               # 01:00 on the 16th in GMT+8
        self.svc.set_birthday(1, 3, 16)
        self.assertEqual([g.user_id for g in self.svc.collect_due_greetings()], [1])

    def test_upcoming_is_sorted_and_filterable(self):
        for uid, (m, d) in {1: (12, 25), 2: (3, 16), 3: (3, 15), 4: (1, 1)}.items():
            self.svc.set_birthday(uid, m, d)
        self.assertEqual([u.user_id for u in self.svc.upcoming()], [3, 2, 1, 4])
        self.assertEqual([u.days_until for u in self.svc.upcoming(2)], [0, 1])
        self.assertEqual([u.user_id for u in self.svc.upcoming(only={1, 4})], [1, 4])

    def test_cake_schedule_persists_and_becomes_due(self):
        self.env.users.update(1, inventory={CAKE_ITEM: 2})
        scheduled = self.svc.schedule_cake(owner_id=1, recipient_id=2, date_text="16/03/2026",
                                           time_text="09:30", message_text="Hi!")
        self.assertEqual(scheduled.local_time, "09:30")
        self.assertEqual(self.env.users.get(1)["inventory"], {CAKE_ITEM: 1})
        self.assertEqual(self.svc.due_cake_celebrations(), [])
        self.env.clock.advance(days=1)                                  # 2026-03-16 12:00 UTC > 01:30 UTC
        (due,) = self.svc.due_cake_celebrations()
        self.assertEqual((due["scheduled_for"], due["message"]), (2, "Hi!"))
        self.svc.mark_cake_celebration(due["id"], "sent")
        self.assertEqual(self.svc.due_cake_celebrations(), [])

    def test_cake_survives_a_restart(self):
        self.env.users.update(1, inventory={CAKE_ITEM: 1})
        self.svc.schedule_cake(owner_id=1, recipient_id=2, date_text="20/03/2026", time_text="", message_text="")
        fresh = BirthdayService(self.env.registry, self.env.celebrations, self.env.users,
                                self.env.settings.birthdays, self.env.clock)       # a brand-new process
        self.env.clock.advance(days=10)
        self.assertEqual(len(fresh.due_cake_celebrations()), 1)

    def test_cake_validation_does_not_consume_the_cake(self):
        self.env.users.update(1, inventory={CAKE_ITEM: 1})
        for date_text, time_text, message in [("31/02/2026", "", "bad date"), ("nope", "", "bad date"),
                                              ("20/03/2026", "25:00", "bad time"), ("01/01/2020", "", "past")]:
            with self.assertRaises(BirthdayError, msg=message):
                self.svc.schedule_cake(owner_id=1, recipient_id=2, date_text=date_text, time_text=time_text, message_text="")
        self.assertEqual(self.env.users.get(1)["inventory"], {CAKE_ITEM: 1})

    def test_cake_requires_owning_a_cake(self):
        with self.assertRaises(BirthdayError):
            self.svc.schedule_cake(owner_id=1, recipient_id=2, date_text="20/03/2026", time_text="", message_text="")


class SessionAndPermissionTests(EnvTestCase):
    def test_session_lock_is_exclusive_and_always_released(self):
        sessions = GamblingSessions()
        with self.assertRaises(RuntimeError):
            with sessions.session(1):
                self.assertIn(1, sessions)
                with self.assertRaises(AlreadyInGame):
                    with sessions.session(1):
                        pass
                raise RuntimeError("game crashed")
        self.assertNotIn(1, sessions)

    def test_admin_policy(self):
        class Perms: administrator = False
        class Role:
            def __init__(self, id): self.id = id
        class Member:
            def __init__(self, admin=False, roles=()):
                self.guild_permissions = type("P", (), {"administrator": admin})()
                self.roles = list(roles)
        policy = AdminPolicy(555)
        self.assertTrue(policy.is_admin(Member(admin=True)))
        self.assertTrue(policy.is_admin(Member(roles=[Role(1), Role(555)])))
        self.assertFalse(policy.is_admin(Member(roles=[Role(1)])))
        self.assertFalse(policy.is_admin(object()))               # plain User (e.g. in a DM)


class ShopServiceTests(EnvTestCase):
    def setUp(self):
        super().setUp()
        from rudhabot.persistence.json_store import JsonStore
        from rudhabot.persistence.specials import SpecialStockRepository, initial_stocks
        from rudhabot.services.shop import CafeMenu, ShopService
        catalog = self.env.catalog
        self.stocks = SpecialStockRepository(JsonStore(self.env.settings.paths.specials,
                                                        lambda: initial_stocks(catalog.special_item_names())))
        self.menu = CafeMenu(catalog, self.stocks, self.env.clock)
        self.shop = ShopService(catalog, self.menu, self.env.users, self.stocks)

    def test_buy_and_sell_regular_item(self):
        self.env.users.update(1, wallet=1000)
        purchase = self.shop.buy(1, "regular lottery ticket", 3)
        self.assertEqual((purchase.total, purchase.special), (300, False))
        account = self.env.users.get(1)
        self.assertEqual((account["wallet"], account["inventory"]), (700, {"regular lottery ticket": 3}))
        sale = self.shop.sell(1, "regular lottery ticket", 2)
        self.assertEqual(sale.total, 100)                       # half of 100, per ticket
        self.assertEqual(self.env.users.get(1)["inventory"], {"regular lottery ticket": 1})

    def test_refusals(self):
        from rudhabot.services.shop import ShopRefused
        self.env.users.update(1, wallet=10)
        for call, code in [(lambda: self.shop.buy(1, "regular lottery ticket", 0), "not_positive"),
                           (lambda: self.shop.buy(1, "unobtainium", 1), "unknown_item"),
                           (lambda: self.shop.buy(1, "regular lottery ticket", 1), "insufficient_wallet"),
                           (lambda: self.shop.sell(1, "boba", 1), "cannot_sell"),
                           (lambda: self.shop.sell(1, "regular lottery ticket", 1), "not_enough_items")]:
            with self.assertRaises(ShopRefused) as ctx:
                call()
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(self.env.users.get(1)["wallet"], 10)

    def test_monthly_special_is_limited_to_one_and_depletes_stock(self):
        from rudhabot.services.shop import ShopRefused
        item = next(iter(self.menu.special_items()))
        price = self.menu.special_items()[item]
        self.env.users.update(1, wallet=price * 5)
        with self.assertRaises(ShopRefused) as ctx:
            self.shop.buy(1, item, 2)
        self.assertEqual(ctx.exception.code, "special_quantity")
        before = self.stocks.stock_of(item)
        self.assertTrue(self.shop.buy(1, item, 1).special)
        self.assertEqual(self.stocks.stock_of(item), before - 1)
        with self.assertRaises(ShopRefused) as ctx:
            self.shop.buy(1, item, 1)
        self.assertEqual(ctx.exception.code, "already_bought_special")

    def test_sold_out_special(self):
        from rudhabot.services.shop import ShopRefused
        item = next(iter(self.menu.special_items()))
        stocks = self.stocks.load(); stocks[item] = 0; self.stocks.save(stocks)
        self.env.users.update(1, wallet=10_000)
        with self.assertRaises(ShopRefused) as ctx:
            self.shop.buy(1, item, 1)
        self.assertEqual(ctx.exception.code, "sold_out")

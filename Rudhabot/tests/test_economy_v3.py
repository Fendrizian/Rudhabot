"""Audit trail, bank (tiers, savings, credit), stock exchange, baccarat/craps, finance insights."""
import json
import os
import random
import tempfile
import unittest
from datetime import date, datetime

from rudhabot.clock import FixedClock
from rudhabot.domain import bank as B
from rudhabot.domain.casino import (BaccaratRound, baccarat_payout, banker_draws, deal_baccarat, hand_total, play_craps,
                                    craps_payout)
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.ledger import Ledger
from rudhabot.persistence.users import UserRepository
from rudhabot.services.audit import AuditService, audit_context
from rudhabot.services.bank import BankRefused, BankService
from rudhabot.services.insights import finance_insights
from rudhabot.services.stocks import StockExchange, TradeRefused, initial_state
from tests.cog_case import CogTestCase


class World(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FixedClock(datetime(2026, 5, 3, 12, 0))
        self.audit = AuditService(Ledger(self.p("ledger.jsonl")), self.clock, channel_id=1)
        self.users = UserRepository(JsonStore(self.p("data.json"), dict), ["SCAY"], audit=self.audit)
        self.bank = BankService(JsonStore(self.p("bank.json"), dict), self.users, self.clock, self.audit)

    def p(self, name):
        return os.path.join(self.tmp.name, name)

    def ledger(self):
        return list(self.audit.ledger.read())


# ================================================================ audit
class AuditTests(World):
    def test_every_write_path_is_recorded_with_its_label(self):
        with audit_context("/work", actor=7, source="command"):
            self.users.update(7, wallet=150)
        self.users.credit_wallet(7, -50)                                 # no context: falls back to the code location
        with self.users.transaction() as data:
            data["7"]["bank"] = 400
            data.setdefault("8", {"wallet": 25, "bank": 0})
        entries = self.ledger()
        self.assertEqual([(e["user"], e["account"], e["delta"]) for e in entries],
                         [("7", "wallet", 150), ("7", "wallet", -50), ("7", "bank", 400), ("8", "wallet", 25)])
        self.assertEqual((entries[0]["reason"], entries[0]["actor"], entries[0]["source"]), ("/work", "7", "command"))
        self.assertIn("test_economy_v3.py", entries[1]["reason"])
        self.assertEqual(entries[0]["after"], 150)

    def test_no_entry_without_a_change_and_crypto_holdings_are_tracked(self):
        self.users.update(1, wallet=0)
        self.assertEqual(self.ledger(), [])
        acc = self.users.get(1)
        acc["crypto"]["SCAY"] = 2.5
        self.users.update(1, wallet=10)
        with self.users.transaction() as data:
            data["1"]["crypto"]["SCAY"] = 4.0
        self.assertIn(("crypto:SCAY", 4.0), [(e["account"], e["delta"]) for e in self.ledger()])

    def test_entries_queue_for_the_channel_and_reach_listeners(self):
        seen = []
        self.audit.listeners.append(seen.append)
        self.users.update(3, wallet=99)
        self.assertEqual(len(self.audit.drain()), 1)
        self.assertEqual(seen[0]["delta"], 99)

    def test_a_torn_last_line_is_skipped(self):
        self.users.update(3, wallet=5)
        with open(self.audit.ledger.path, "a") as f:
            f.write('{"user": "3", "delta"')
        self.assertEqual(len(self.ledger()), 1)


# ================================================================ bank
class BankRuleTests(unittest.TestCase):
    def test_tiers_match_the_spec(self):
        spec = [(t.key, t.daily_interest, t.monthly_limit) for t in B.TIERS]
        self.assertEqual(spec, [("red", 0.0005, 10_000), ("silver", 0.001, 20_000), ("gold", 0.002, 50_000),
                                ("platinum", 0.003, 100_000)])
        self.assertTrue(all(t.emoji.startswith("<:bankcard_") for t in B.TIERS))

    def test_interest_compounds_carries_fractions_and_is_capped(self):
        red = B.TIER_BY_KEY["red"]
        whole, carry = B.accrue(1000, 0.0, red, 1)                       # 0.5 ra: carried, not lost
        self.assertEqual((whole, round(carry, 4)), (0, 0.5))
        whole, carry = B.accrue(1000, carry, red, 1)
        self.assertEqual(whole, 1)
        capped, _ = B.accrue(10_000_000, 0.0, red, 1)
        self.assertEqual(capped, int(red.interest_cap * red.daily_interest))

    def test_statement_rolls_monthly_and_late_interest_freezes_once(self):
        credit = {"balance": 1000}
        self.assertIsNone(B.roll_statement(credit, date(2026, 6, 2)))
        self.assertEqual((credit["statement"], credit["due"]), (1000, "2026-06-11"))
        self.assertEqual(B.roll_statement(credit, date(2026, 6, 12)), 50)
        self.assertTrue(credit["frozen"])
        self.assertIsNone(B.roll_statement(credit, date(2026, 6, 20)))   # charged once


class BankServiceTests(World):
    def test_monthly_deposit_limit_resets_next_month(self):
        self.users.update(1, wallet=25_000)
        self.assertEqual(self.bank.transfer(1, 8_000, "deposit"), 8_000)
        with self.assertRaises(BankRefused) as refused:
            self.bank.transfer(1, 5_000, "deposit")
        self.assertIn("2,000 left", str(refused.exception))
        self.assertEqual(self.bank.transfer(1, None, "deposit"), 2_000)  # 'all' = what the limit allows
        self.clock.set(datetime(2026, 6, 1, 9))
        self.assertEqual(self.bank.transfer(1, 3_000, "deposit"), 3_000)

    def test_savings_interest_and_one_withdrawal_a_day(self):
        self.users.update(1, bank=20_000)
        self.bank.savings_deposit(1, 20_000)
        self.clock.advance(days=10)
        profile = self.bank.account(1)
        self.assertGreater(profile["savings"], 20_000)
        self.bank.savings_withdraw(1, 100)
        with self.assertRaises(BankRefused):
            self.bank.savings_withdraw(1, 100)
        self.clock.advance(hours=25)
        self.bank.savings_withdraw(1, 100)
        reasons = [e["reason"] for e in self.ledger() if e["account"] == "savings"]
        self.assertTrue(any(r.startswith("savings interest") for r in reasons))

    def test_upgrade_needs_net_worth_and_fee(self):
        self.users.update(1, bank=20_000)
        with self.assertRaises(BankRefused):
            self.bank.upgrade(1)                                         # net worth 20k < 25k
        self.users.update(1, wallet=6_000)
        self.assertEqual(self.bank.upgrade(1).key, "silver")
        self.assertEqual(self.users.get(1)["bank"], 17_500)

    def test_credit_card_limit_freeze_and_payment(self):
        self.assertEqual(self.bank.charge(1, 1_500, "cake"), 1_500)
        with self.assertRaises(BankRefused):
            self.bank.charge(1, 600, "more")                              # red limit 2,000
        self.clock.set(datetime(2026, 6, 15))                            # statement of June is now overdue
        with self.assertRaises(BankRefused) as frozen:
            self.bank.charge(1, 10, "x")
        self.assertIn("frozen", str(frozen.exception))
        self.users.update(1, wallet=5_000)
        self.bank.pay_card(1, None)
        self.assertEqual(self.bank.account(1)["credit"]["balance"], 0)
        self.assertFalse(self.bank.account(1)["credit"]["frozen"])
        self.bank.charge(1, 10, "after")


class InsightTests(unittest.TestCase):
    def test_insights_are_personal_and_prioritised(self):
        now = datetime(2026, 5, 20, 12)
        entries = [{"ts": f"2026-05-{d:02d}T10:00:00", "account": "wallet", "delta": -300, "reason": "/buy"}
                   for d in range(1, 15)] + [{"ts": "2026-05-19T10:00:00", "account": "wallet", "delta": -9000, "reason": "/buy"}]
        profile = {"tier": "red", "savings": 0, "credit": {"balance": 1800, "statement": 1800, "due": "2026-05-22"}}
        tips = finance_insights(entries, profile, bank_balance=5000, now=now)
        self.assertIn("due in 2 day(s)", tips[0])
        self.assertEqual(len(tips), 2)


# ================================================================ stocks
class StockTests(World):
    def setUp(self):
        super().setUp()
        self.ex = StockExchange(JsonStore(self.p("stocks.json"), lambda: initial_state(self.clock.now().isoformat())),
                                self.users, self.clock, random.Random(4), self.audit)

    def run_days(self, days, per_user, users):
        for _ in range(days * 24):
            self.clock.advance(hours=1)
            for u in range(users):
                self.ex.record_revenue("NAS", per_user / 24, user_id=u)

    def test_broad_buying_raises_value_more_than_one_whale(self):
        start = self.ex.quote("NAS")["value"]
        self.run_days(4, 2_500, 20)
        broad = self.ex.quote("NAS")["value"]
        self.assertGreater(broad, start)
        self.clock.advance(days=40)
        self.run_days(4, 50_000, 1)
        self.assertLess(self.ex.quote("NAS")["value"], broad)

    def test_prices_never_reach_zero_even_after_long_downtime(self):
        self.clock.advance(days=120)
        q = self.ex.quote("JOOT")
        self.assertGreaterEqual(q["price"], 80 * 0.10)
        self.assertGreater(q["value"], 0)

    def test_round_trip_trades_lose_money_and_shares_are_audited(self):
        self.users.update(1, wallet=5_000_000)
        bought = self.ex.buy(1, "NAS", 10_000)
        sold = self.ex.sell(1, "NAS", None)
        self.assertLess(sold.total, bought.total)
        self.assertEqual(self.ex.portfolio(1), [])
        shares = [e for e in self.ledger() if e["account"] == "shares:NAS"]
        self.assertEqual([e["delta"] for e in shares], [10_000, -10_000])
        with self.assertRaises(TradeRefused):
            self.ex.buy(1, "NAS", 10**7)


# ================================================================ casino
class CasinoRuleTests(unittest.TestCase):
    def test_banker_tableau(self):
        self.assertTrue(banker_draws(5, None) and not banker_draws(6, None))
        self.assertFalse(banker_draws(3, 8))
        self.assertTrue(banker_draws(4, 2) and not banker_draws(4, 1))
        self.assertTrue(banker_draws(6, 6) and not banker_draws(6, 5))
        self.assertFalse(banker_draws(7, 6))

    def test_baccarat_values_and_payouts(self):
        self.assertEqual(hand_total(["K♠", "9♥"]), 9)
        self.assertEqual(hand_total(["7♠", "8♥"]), 5)
        tie = BaccaratRound(["9♠", "K♦"], ["9♥", "Q♣"], "tie", True)
        self.assertEqual((baccarat_payout("tie", 100, tie), baccarat_payout("player", 100, tie)), (800, 0))
        banker = BaccaratRound(["2♠", "3♦"], ["9♥", "Q♣"], "banker", True)
        self.assertEqual((baccarat_payout("banker", 100, banker), baccarat_payout("player", 100, banker)), (95, -100))

    def test_long_run_house_edges(self):
        rng = random.Random(0)
        n = 30_000
        pnl = sum(baccarat_payout("banker", 100, deal_baccarat(rng)) for _ in range(n)) / (n * 100)
        self.assertTrue(-0.03 < pnl < 0.0)
        pnl = sum(craps_payout(100, play_craps("pass", rng)) for _ in range(n)) / (n * 100)
        self.assertTrue(-0.035 < pnl < 0.005)

    def test_craps_resolution(self):
        class Dice(random.Random):
            def __init__(self, rolls):
                super().__init__(0)
                self.rolls = list(rolls)

            def randint(self, a, b):
                return self.rolls.pop(0)
        self.assertEqual(play_craps("pass", Dice([3, 4])).outcome, "win")             # come-out 7
        self.assertEqual(play_craps("dontpass", Dice([6, 6])).outcome, "push")        # 12 on don't pass
        r = play_craps("pass", Dice([2, 2, 1, 1, 3, 1]))                              # point 4, then 2, then 4
        self.assertEqual((r.point, r.outcome), (4, "win"))
        r = play_craps("field", Dice([6, 6]))
        self.assertEqual((r.outcome, craps_payout(10, r)), ("win", 30))


class EconomyCommandTests(CogTestCase):
    async def test_work_and_slots_are_labelled_in_the_ledger(self):
        await self.run_command("work", self.alice)
        entries = list(self.s.audit.ledger.read())
        self.assertTrue(entries and entries[-1]["reason"] == "/work" and entries[-1]["actor"] == str(self.alice.id))

    async def test_deposit_respects_the_card_limit(self):
        self.fund(self.alice, 50_000)
        await self.run_command("deposit", self.alice, amount="all")
        self.assertEqual(self.account(self.alice)["bank"], 10_000)

    async def test_buy_with_card_charges_credit_and_moves_the_stock(self):
        before = self.s.stocks.quote("NAS")["revenue_rate"]
        item = next(iter(self.s.catalog.shop_items))
        price = self.s.catalog.shop_items[item]
        if price > 2000:
            self.skipTest("cheapest item is above the red credit limit")
        it = await self.run_command("buy", self.alice, item=item, quantity=1,
                                    pay_with=__import__("discord").app_commands.Choice(name="Credit card", value="card"))
        self.assertIn("credit card", it.text())
        self.assertEqual(self.s.bank.account(self.alice.id)["credit"]["balance"], price)
        self.assertGreater(self.s.stocks.quote("NAS")["revenue_rate"], before)

    async def test_baccarat_settles_the_wallet(self):
        self.fund(self.alice, 1000)
        choice = __import__("discord").app_commands.Choice(name="Banker", value="banker")
        await self.run_command("baccarat", self.alice, bet="100", on=choice)
        self.assertIn(self.account(self.alice)["wallet"], (1000, 1095, 900))
        self.assertNotIn(self.alice.id, self.s.sessions)

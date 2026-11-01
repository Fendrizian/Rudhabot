"""Regression tests for the bugs found in the November 2026 full review (each test names the bug it pins down)."""
import json
from datetime import datetime

from rudhabot.services.bank import BankRefused
from tests.test_economy_v3 import World


class BankRefreshIsSavedEvenWhenRefusedTests(World):
    def test_late_interest_is_charged_and_audited_once_across_refused_operations(self):
        # Bug: refreshing the card (late interest, freeze) happened in memory, the audit entry was written, then a
        # refusal skipped the save, so every refused attempt logged another late-interest charge.
        self.bank.charge(1, 1_000, "test")                                   # April balance
        self.clock.set(datetime(2026, 6, 20, 12))                             # statement due 11 June, unpaid
        for _ in range(3):
            with self.assertRaises(BankRefused):
                self.bank.charge(1, 10, "test")                               # frozen: refused
        late = [e for e in self.ledger() if e["reason"] == "credit card late interest"]
        self.assertEqual(len(late), 1)
        self.assertEqual(self.bank.account(1)["credit"]["balance"], 1_050)    # 5% charged exactly once

    def test_old_savings_move_into_the_bank_exactly_once_even_if_the_first_operation_is_refused(self):
        # Bug: the fold credited the bank balance immediately but its removal from bank.json was only saved on
        # success, so each refused command credited the savings again.
        with open(self.p("bank.json"), "w", encoding="utf-8") as handle:
            json.dump({"1": {"tier": "red", "savings": 5_000}}, handle)
        for _ in range(3):
            with self.assertRaises(BankRefused):
                self.bank.transfer(1, 999_999, "withdraw")                    # more than the bank holds: refused
        self.assertEqual(self.users.get(1)["bank"], 5_000)
        folds = [e for e in self.ledger() if e["account"] == "savings"]
        self.assertEqual([(e["delta"], e["reason"]) for e in folds], [(-5_000, "savings merged into bank")])

    def test_interest_is_remembered_before_it_is_paid(self):
        self.users.update(1, wallet=10_000)
        self.bank.transfer(1, 10_000, "deposit")                              # a deposit opens the profile today
        self.clock.set(datetime(2026, 5, 4, 12))
        self.assertEqual(self.bank.apply_daily_interest(), 1)
        self.assertEqual(self.bank.apply_daily_interest(), 0)                 # same day: nothing more
        self.assertEqual(self.users.get(1)["bank"], 10_005)

    def test_looking_up_a_bank_account_creates_no_data(self):
        self.bank.account(77)
        with open(self.p("bank.json"), encoding="utf-8") as handle:
            self.assertNotIn("77", json.load(handle))


class ReviewSweepTests(__import__("unittest").TestCase):
    def test_a_burst_inside_one_minute_is_still_judged_after_the_user_goes_quiet(self):
        # Bug: a user was re-judged at most once a minute, and only when they did something else. A burst of activity
        # that ended within a minute (e.g. 30 big wins in 50 seconds, then silence) was never looked at.
        import os
        import tempfile
        from rudhabot.clock import FixedClock
        from rudhabot.persistence.json_store import JsonStore
        from rudhabot.persistence.ledger import Ledger
        from rudhabot.services.review import ReviewService
        with tempfile.TemporaryDirectory() as tmp:
            clock = FixedClock(datetime(2026, 5, 1, 12))
            svc = ReviewService(JsonStore(os.path.join(tmp, "r.json"), dict), Ledger(os.path.join(tmp, "l.jsonl")),
                                Ledger(os.path.join(tmp, "f.jsonl")), clock)
            svc.refit()                                                      # cold start: hard rules apply
            for _ in range(20):                                              # 20 failures within 20 seconds
                clock.advance(seconds=1)
                svc.on_failure(42, "/slots: CheckFailure")
            self.assertEqual(svc.signals("open"), [])                        # only the first one was judged
            clock.advance(seconds=61)
            opened = svc.sweep()                                             # the audit loop's sweep
            self.assertEqual(len(opened), 1)
            self.assertEqual(opened[0]["kind"], "rule")
            self.assertEqual(svc.sweep(), [])                                # nothing left to judge


from tests.cog_case import CogTestCase  # noqa: E402
from tests.fakes import FakeInteraction  # noqa: E402


class NoSpendingDuringAGameTests(CogTestCase):
    """Bug: a game settles its bet at the end against the current wallet (never below zero). /tcg pack,
    /tcg wish shards and the /shop and /cafe buttons didn't check for an open game, so a player could bet their whole
    wallet, spend it while the game ran, and lose nothing."""

    async def test_spending_commands_and_buttons_are_refused_while_a_bet_is_open(self):
        from rudhabot.views.cafe import CafeQuantityModal
        from rudhabot.views.shop import BuyButton, ItemRow
        self.fund(self.alice, 5_000)
        self.s.sessions.start(self.alice.id)
        try:
            tcg = self.bot.tree.get_command("tcg")
            pack = next(c for c in tcg.commands if c.name == "pack")
            it = FakeInteraction(self.bot, self.alice)
            it.command = pack
            with self.assertRaises(Exception):
                await pack._check_can_run(it)                                  # the not-in-game check refuses
            view = ItemRow(self.s, [("baseball cap", 50)], 0, 1, self.alice.id)
            press = FakeInteraction(self.bot, self.alice)
            await next(c for c in view.children if isinstance(c, BuyButton)).callback(press)
            self.assertIn("already playing", press.text())
            modal = CafeQuantityModal(self.s, "toast")
            modal.quantity._value = "2"
            order = FakeInteraction(self.bot, self.alice)
            await modal.on_submit(order)
            self.assertIn("already playing", order.text())
            self.assertEqual(self.account(self.alice)["wallet"], 5_000)        # nothing was spent
        finally:
            self.s.sessions.end(self.alice.id)


class FinesNeverMakeAWalletNegativeTests(CogTestCase):
    async def test_crime_and_rob_fines_come_from_the_wallet_then_the_bank(self):
        # Bug: the fine was sized on wallet + bank but taken from the wallet alone, so it could go far below zero.
        self.fund(self.alice, 10, bank=10_000)
        self.script.rolls += [100, 20]                       # caught (roll > 70), fined 20% of 10,010 = 2,002
        await self.run_command("crime", self.alice)
        account = self.account(self.alice)
        self.assertEqual((account["wallet"], account["bank"]), (0, 10_010 - 2_002 - 0))

    async def test_money_cannot_be_sent_to_a_bot(self):
        from tests.fakes import FakeUser
        robot = FakeUser(5005, "robot")
        robot.bot = True
        self.fund(self.alice, 100)
        it = await self.run_command("send", self.alice, target=robot, amount="50")
        self.assertIn("Bots", it.text())
        self.assertEqual(self.account(self.alice)["wallet"], 100)


class GamesNeverLeaveAPlayerLockedTests(CogTestCase):
    """Bug: crash and high-low ended the player's session only *after* editing the Discord message, so a failed edit
    (message deleted, a 5xx, rate limiting) left the player locked out of every money command until a restart."""

    def _failing_edits(self, it):
        from types import SimpleNamespace
        import discord

        async def fail(**_):
            raise discord.HTTPException(SimpleNamespace(status=404, reason="Not Found"), "Unknown Message")
        it.edit_original_response = fail

    async def test_highlow_timeout_unlocks_and_refunds_even_if_the_message_is_gone(self):
        self.fund(self.alice, 500)
        it = await self.run_command("highlow", self.alice, bet="200")
        view = it.log[-1][1]["view"]
        self.assertIn(self.alice.id, self.s.sessions)
        self._failing_edits(view.interaction)
        await view.on_timeout()
        self.assertNotIn(self.alice.id, self.s.sessions)
        self.assertEqual(self.account(self.alice)["wallet"], 500)

    async def test_crash_timeout_unlocks_and_refunds_even_if_the_message_is_gone(self):
        self.fund(self.alice, 500)
        it = await self.run_command("crash", self.alice, bet="200")
        view = it.log[-1][1]["view"]
        view.task.cancel()
        self._failing_edits(view.interaction)
        await view.on_timeout()
        self.assertNotIn(self.alice.id, self.s.sessions)
        self.assertEqual(self.account(self.alice)["wallet"], 500)


class CryptoForBrandNewMembersTests(CogTestCase):
    async def test_a_new_members_first_trade_is_refused_politely(self):
        # Trades used to look the account up in a whole-file transaction; a member who had never done anything had no
        # stored account, so their first /buycoin raised KeyError ("Something went wrong").
        from rudhabot.services.crypto_market import TradeRefused
        with self.assertRaises(TradeRefused):
            self.s.crypto.buy(4242, "SCAY", 100)
        with self.assertRaises(TradeRefused):
            self.s.crypto.sell(4242, "SCAY", "1")
        self.fund(self.bob, 1_000)
        trade = self.s.crypto.buy(self.bob.id, "SCAY", 500)
        self.assertEqual(self.account(self.bob)["wallet"], 500)
        self.assertAlmostEqual(self.account(self.bob)["crypto"]["SCAY"], trade.quantity)
        with self.assertRaises(TradeRefused):
            self.s.crypto.sell(self.bob.id, "SCAY", "nan")                  # float("nan") used to slip past "<= 0"


class IntentCommandsExistTests(CogTestCase):
    async def test_every_intent_points_at_a_real_command(self):
        # Bug: the "card" intent named `/card info`, which doesn't exist (it is `/bank card info`), so its button
        # explained a command nobody could run.
        from discord import app_commands
        from rudhabot.cogs.assistant import find_command
        from rudhabot.ml.intent_data import INTENTS
        for intent, (_, path, _) in INTENTS.items():
            command = find_command(self.bot.tree, path)
            self.assertIsNotNone(command, f"{intent} -> /{path}")
            self.assertNotIsInstance(command, app_commands.Group, f"{intent} -> /{path} is a group, not a command")

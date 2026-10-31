"""Command-level tests: the slots upgrade, background jobs, and every pre-existing bug that was fixed."""
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from rudhabot.ui.messages import MSG_INVALIDBET, MSG_NONPOS, MSG_NOWALLET, MSG_ONGAME
from rudhabot.ui.theme import Colors
from tests.cog_case import CogTestCase
from tests.fakes import FakeGuild, FakeInteraction
from tests.legacy_slots_reference import legacy_spin

CHERRY, LEMON, ORANGE, SEVEN = 0, 1, 2, 6


class SlotsCommandTests(CogTestCase):
    async def play(self, reels, bet="100", wallet=1000):
        self.fund(self.alice, wallet)
        self.script.reels += reels
        return await self.run_command("slots", self.alice, bet=bet)

    async def test_pair_pays_double_and_uses_four_api_calls_with_a_suspense_frame(self):
        it = await self.play([CHERRY, CHERRY, LEMON])
        self.assertEqual(self.account(self.alice)["wallet"], 1100)               # x2 on 100 => +100
        self.assertEqual([kind for kind, _ in it.log], ["send", "edit", "edit", "edit"])
        frames = [k["embed"] for _, k in it.log]
        self.assertIn("Spinning reel 1 of 3", frames[0].description)
        self.assertIn("🌀", frames[0].description)
        self.assertIn("Payouts", [f.name for f in frames[0].fields])             # paytable while the first reel spins up
        self.assertIn("Spinning reel 2 of 3", frames[1].description)
        self.assertIn("Two matching", frames[2].description)                     # suspense: first two reels equal
        self.assertEqual(frames[2].color.value, 0xE67E22)
        final = frames[3]
        self.assertIn("A PAIR OF CHERRIES", final.description)
        self.assertIn("You win!", final.description)
        self.assertEqual({f.name: f.value for f in final.fields}["Balance"].endswith("1100"), True)

    async def test_triple_sevens_is_the_gold_jackpot_x50(self):
        it = await self.play([SEVEN, SEVEN, SEVEN], bet="10")
        self.assertEqual(self.account(self.alice)["wallet"], 1000 + 10 * 50 - 10)
        final = it.last_embed
        self.assertIn("JACKPOT", final.description)
        self.assertIn("(x50)", final.description)
        self.assertEqual(final.color.value, Colors.GOLD)

    async def test_a_miss_loses_the_bet(self):
        it = await self.play([CHERRY, LEMON, ORANGE], bet="250")
        self.assertEqual(self.account(self.alice)["wallet"], 750)
        self.assertIn("NO MATCH", it.last_embed.description)
        self.assertIn("You lose!", it.last_embed.description)
        self.assertEqual(it.last_embed.color.value, Colors.LOSS)

    async def test_bet_validation_messages_are_unchanged_and_never_leave_a_lock(self):
        for bet, message in (("abc", MSG_INVALIDBET), ("0", MSG_NONPOS), ("-5", MSG_NONPOS), ("5000", MSG_NOWALLET)):
            self.fund(self.alice, 1000)
            it = await self.run_command("slots", self.alice, bet=bet)
            self.assertEqual(it.embeds[0].description, message, bet)
            self.assertEqual(it.ephemeral(), [True])
            self.assertNotIn(self.alice.id, self.s.sessions)
        self.assertEqual(self.account(self.alice)["wallet"], 1000)

    async def test_all_in_bets_the_whole_wallet(self):
        await self.play([CHERRY, LEMON, ORANGE], bet="all", wallet=300)
        self.assertEqual(self.account(self.alice)["wallet"], 0)

    async def test_cannot_start_a_second_game(self):
        self.s.sessions.start(self.alice.id)
        it = await self.run_command("slots", self.alice, bet="10")
        self.assertEqual(it.embeds[0].description, MSG_ONGAME)

    async def test_the_lock_is_held_during_play_and_released_after(self):
        seen = []
        original = self.s.slots.spin_reel
        self.s.slots.spin_reel = lambda: (seen.append(self.alice.id in self.s.sessions), original())[1]
        await self.play([CHERRY, LEMON, ORANGE])
        self.assertEqual(seen, [True, True, True])
        self.assertNotIn(self.alice.id, self.s.sessions)

    async def test_command_payouts_equal_the_original_algorithm_for_100_seeds(self):
        for seed in range(100):
            self.fund(self.alice, 1000)
            self.s.slots._rng = random.Random(seed)
            _, multiplier = legacy_spin(random.Random(seed))
            await self.run_command("slots", self.alice, bet="100")
            expected = 1000 + (100 * multiplier - 100 if multiplier else -100)
            self.assertEqual(self.account(self.alice)["wallet"], expected, f"seed {seed}")

    async def test_money_received_during_the_spin_is_not_overwritten(self):
        """The original wrote back a wallet snapshot taken before a ~6 second animation."""
        self.fund(self.alice, 1000)
        self.script.reels += [CHERRY, LEMON, ORANGE]

        original_sleep = __import__("asyncio").sleep
        async def gift_during_spin(*a, **k):
            if not getattr(self, "_gifted", False):
                self._gifted = True
                self.s.users.credit_wallet(self.alice.id, 500)           # e.g. someone /send's money meanwhile
            await original_sleep(0)
        import asyncio
        asyncio.sleep = gift_during_spin
        await self.run_command("slots", self.alice, bet="100")
        self.assertEqual(self.account(self.alice)["wallet"], 1000 + 500 - 100)


class LockFixTests(CogTestCase):
    async def test_a_refused_highlow_bet_no_longer_locks_the_player_out(self):
        """Regression: highlow and crash took the lock before validating and never released it on refusal."""
        for command in ("highlow", "crash"):
            for bet in ("abc", "0", "999999"):
                self.fund(self.alice, 100)
                await self.run_command(command, self.alice, bet=bet)
                self.assertNotIn(self.alice.id, self.s.sessions, f"{command} {bet}")
        it = await self.run_command("balance", self.alice, user=None)
        self.assertIn("Balance", it.text())

    async def test_highlow_takes_the_bet_and_refunds_it_on_timeout(self):
        self.fund(self.alice, 100)
        it = await self.run_command("highlow", self.alice, bet="40")
        self.assertEqual(self.account(self.alice)["wallet"], 60)
        self.assertIn(self.alice.id, self.s.sessions)
        view = it.log[0][1]["view"]
        self.assertFalse(await view.interaction_check(FakeInteraction(self.bot, self.bob)))   # only the player may click
        await view.on_timeout()
        self.assertEqual(self.account(self.alice)["wallet"], 100)
        self.assertNotIn(self.alice.id, self.s.sessions)
        self.assertIn("Final wallet: 100ra", it.log[-1][1]["embed"].footer.text)              # shows the refunded balance

    async def test_while_playing_guarded_commands_are_refused_with_the_standard_message(self):
        self.s.sessions.start(self.alice.id)
        for name, kwargs in (("balance", {"user": None}), ("work", {}), ("daily", {}), ("shop", {}), ("lottery", {})):
            it = await self.run_command(name, self.alice, **kwargs)
            self.assertEqual(it.embeds[0].description, MSG_ONGAME, name)
            self.assertEqual(it.ephemeral(), [True])
            self.assertIsNotNone(it.embeds[0].author.name)
        self.s.sessions.end(self.alice.id)
        self.assertIn("Balance", (await self.run_command("balance", self.alice, user=None)).text())


class FixedBugTests(CogTestCase):
    async def test_negative_crypto_purchase_cannot_mint_money(self):
        self.fund(self.alice, 100)
        it = await self.run_command("buycoin", self.alice, coin="SCAY", amount=-1_000_000)
        self.assertIn("must be positive", it.text())
        self.assertEqual(it.ephemeral(), [True])
        self.assertEqual(self.account(self.alice)["wallet"], 100)

    async def test_crypto_round_trip_through_the_commands(self):
        self.fund(self.alice, 1000)
        await self.run_command("buycoin", self.alice, coin="SCAY", amount=500)
        self.assertEqual(self.account(self.alice)["wallet"], 500)
        it = await self.run_command("sellcoin", self.alice, coin="SCAY", amount="all")
        self.assertIn("You sold 5.0000 SCAY", it.text())
        self.assertEqual(self.account(self.alice)["wallet"], 1000)

    async def test_a_loan_survives_other_commands(self):
        """Regression: any command that updated the account silently cancelled the player's loan."""
        self.fund(self.alice, 0)
        await self.run_command("loan", self.alice, amount=500, days=5)
        await self.run_command("work", self.alice)
        await self.run_command("daily", self.alice)
        await self.run_command("deposit", self.alice, amount="10")
        it = await self.run_command("loaninfo", self.alice)
        self.assertIn("Loan Status", it.text())
        self.assertIsNotNone(self.account(self.alice)["loan"])

    async def test_overpaying_a_loan_only_charges_what_is_owed(self):
        """Regression: paying more than you owed used to burn the difference."""
        self.fund(self.alice, 0)
        await self.run_command("loan", self.alice, amount=500, days=5)           # wallet 500, owes 500
        self.fund(self.alice, 5000, loan=self.account(self.alice)["loan"])
        it = await self.run_command("pay", self.alice, amount="99999")           # more than the wallet: refused
        self.assertIn("don't have enough money", it.text())
        self.assertEqual(self.account(self.alice)["wallet"], 5000)
        it = await self.run_command("pay", self.alice, amount="800")             # more than owed: capped at 500
        self.assertIn("fully repaid", it.text())
        self.assertEqual(self.account(self.alice)["wallet"], 4500)
        self.assertIsNone(self.account(self.alice)["loan"])

    async def test_create_is_admin_only_and_confirms(self):
        guild = FakeGuild()
        it = await self.run_command("create", self.alice, guild=guild)
        self.assertIn("don't have permission", it.text())
        self.assertEqual(guild.created, [])
        it = await self.run_command("create", self.admin, guild=guild)
        self.assertEqual(len(guild.created), 1)
        self.assertIn("Created", it.text())
        it = await self.run_command("create", self.admin, guild=None)
        self.assertIn("must be used in a server", it.text())

    async def test_add_and_remove_money_are_admin_only(self):
        it = await self.run_command("add-money", self.alice, target=self.bob, amount=100, location="wallet")
        self.assertIn("don't have permission", it.text())
        self.assertEqual(self.account(self.bob)["wallet"], 0)
        await self.run_command("add-money", self.admin, target=self.bob, amount=100, location="wallet")
        self.assertEqual(self.account(self.bob)["wallet"], 100)
        await self.run_command("remove-money", self.admin, target=self.bob, amount=30, location="wallet")
        self.assertEqual(self.account(self.bob)["wallet"], 70)

    async def test_unexpected_errors_are_reported_instead_of_hanging(self):
        self.s.users.get = Mock(side_effect=RuntimeError("disk on fire"))
        with self.assertLogs("rudhabot", level="ERROR"):                         # the failure is logged for operators
            it = await self.run_command("balance", self.alice, user=None)
        self.assertIn("Something went wrong", it.text())
        self.assertEqual(it.ephemeral(), [True])


class LoanJobTests(CogTestCase):
    def guild(self):
        notifications = SimpleNamespace(id=self.s.settings.channels.notifications)
        member = SimpleNamespace(add_roles=AsyncMock())
        role = SimpleNamespace(id=self.s.settings.loans.bankrupt_role_id)
        guild = FakeGuild(channels={notifications.id: notifications}, members={self.alice.id: member},
                          roles={role.id: role})
        self.bot.guild_id = guild.id
        self.bot.get_guild = lambda gid: guild
        return guild, notifications, member, role

    async def test_bankruptcy_pass_takes_assets_announces_and_assigns_the_role(self):
        guild, channel, member, role = self.guild()
        self.s.loans.open(self.alice.id, 1000, 2)
        self.s.users.update(self.alice.id, wallet=10, bank=10)
        self.clock.advance(days=3)
        await self.cog("LoansCog").settle_loans()
        account = self.account(self.alice)
        self.assertEqual((account["wallet"], account["bank"], account["loan"]), (0, 0, None))
        self.assertIsNotNone(account["loan_ban_until"])
        (call,) = self.s.messenger.post.await_args_list
        self.assertIs(call.args[0], channel)
        self.assertIn("Bankruptcy", call.kwargs["embed"].title)
        self.assertEqual(call.kwargs["content"], "<@1001>")
        member.add_roles.assert_awaited_once_with(role)

    async def test_autopay_pass(self):
        guild, channel, member, role = self.guild()
        self.s.loans.open(self.alice.id, 1000, 2)
        self.s.users.update(self.alice.id, wallet=5000)
        self.clock.advance(days=3)
        await self.cog("LoansCog").settle_loans()
        self.assertEqual(self.account(self.alice)["wallet"], 5000 - 1040)
        (call,) = self.s.messenger.post.await_args_list
        self.assertIn("Auto-Paid", call.kwargs["embed"].title)
        member.add_roles.assert_not_called()

    async def test_user_money_changes_during_notifications_are_not_overwritten(self):
        """The old job saved a stale snapshot of EVERY account after awaiting DMs."""
        guild, channel, member, role = self.guild()
        self.s.loans.open(self.alice.id, 1000, 2)
        self.s.users.update(self.alice.id, wallet=5000)
        self.s.users.update(self.bob.id, wallet=100)
        self.clock.advance(days=3)
        async def bob_earns_while_notifying(*a, **k):
            self.s.users.credit_wallet(self.bob.id, 777)
            return True
        self.s.messenger.post.side_effect = bob_earns_while_notifying
        await self.cog("LoansCog").settle_loans()
        self.assertEqual(self.account(self.bob)["wallet"], 877)

    async def test_warning_pass_warns_once(self):
        guild, channel, member, role = self.guild()
        self.s.loans.open(self.alice.id, 1000, 3)
        self.clock.advance(days=2, hours=1)
        cog = self.cog("LoansCog")
        await cog.warn_loans()
        (call,) = self.s.messenger.post.await_args_list
        self.assertIn("Warning", call.kwargs["embed"].title)
        self.assertTrue(self.account(self.alice)["loan_warning_sent"])
        await cog.warn_loans()
        self.assertEqual(self.s.messenger.post.await_count, 1)

"""Command-level tests for the new features: lottery tickets and birthdays."""
from discord import app_commands

from rudhabot.domain.lottery import GOLDEN_TICKET, REGULAR_TICKET
from rudhabot.services.birthdays import CAKE_ITEM
from tests.cog_case import CogTestCase

REGULAR, GOLDEN = "regular lottery ticket", "golden lottery ticket"
ZONK_ROLL = REGULAR_TICKET.table.win_weight             # first roll that is no prize
JACKPOT_ROLL = REGULAR_TICKET.table.win_weight - 1      # last winning roll = the jackpot tier
GOLD_JACKPOT_ROLL = GOLDEN_TICKET.table.win_weight - 1


class LotteryUseTests(CogTestCase):
    async def test_a_zonk_wins_nothing_and_uses_the_ticket(self):
        self.fund(self.alice, 50, inventory={REGULAR: 2})
        self.script.rolls += [ZONK_ROLL]
        it = await self.run_command("use", self.alice, item="regular lottery ticket", quantity=1)
        self.assertIn("Zonk", it.text())
        self.assertEqual(self.account(self.alice)["wallet"], 50)
        self.assertEqual(self.account(self.alice)["inventory"], {REGULAR: 1})
        self.s.messenger.post.assert_not_called()

    async def test_a_win_is_paid_to_the_wallet(self):
        self.fund(self.alice, 50, inventory={REGULAR: 1})
        self.script.rolls += [0]                                   # the smallest prize: 10
        it = await self.run_command("use", self.alice, item="regular lottery ticket")
        self.assertIn("won", it.text())
        self.assertEqual(self.account(self.alice)["wallet"], 60)
        self.assertNotIn(REGULAR, self.account(self.alice)["inventory"])

    async def test_aliases_resolve_to_the_regular_ticket(self):
        self.fund(self.alice, 0, inventory={REGULAR: 1})
        self.script.rolls += [0]
        await self.run_command("use", self.alice, item="lottery ticket")
        self.assertEqual(self.account(self.alice)["wallet"], 10)

    async def test_golden_jackpot_pays_100_million_and_is_announced(self):
        self.fund(self.alice, 0, inventory={GOLDEN: 1})
        self.script.rolls += [GOLD_JACKPOT_ROLL]
        it = await self.run_command("use", self.alice, item="golden ticket")
        self.assertEqual(self.account(self.alice)["wallet"], 100_000_000)
        self.assertIn("JACKPOT", it.text())
        (call,) = self.s.messenger.post.await_args_list
        self.assertEqual(call.args[0], self.s.settings.channels.lottery)       # posted to the lottery channel
        self.assertIn("JACKPOT", call.kwargs["embed"].title)
        self.assertIn("100000000", call.kwargs["embed"].description)
        self.assertEqual([w["amount"] for w in self.s.lottery.recent_jackpots()], [100_000_000])

    async def test_batch_summary_totals(self):
        self.fund(self.alice, 0, inventory={REGULAR: 5})
        self.script.rolls += [0, 0, ZONK_ROLL, ZONK_ROLL, REGULAR_TICKET.table.tiers[0].weight]   # 10, 10, zonk, zonk, 20
        it = await self.run_command("use", self.alice, item="regular lottery ticket", quantity=5)
        embed = it.last_embed
        self.assertIn("5 tickets", embed.title)
        fields = {f.name: f.value for f in embed.fields}
        self.assertTrue(fields["Won"].endswith("40"))
        self.assertTrue(fields["Ticket value"].endswith("500"))
        self.assertIn("-", fields["Net"])
        self.assertIn("Zonk × 2", embed.description)
        self.assertEqual(self.account(self.alice)["wallet"], 40)

    async def test_cannot_use_tickets_you_do_not_own(self):
        self.fund(self.alice, 5, inventory={REGULAR: 1})
        it = await self.run_command("use", self.alice, item="regular lottery ticket", quantity=2)
        self.assertIn("don't have enough", it.text())
        self.assertEqual(it.ephemeral(), [True])
        self.assertEqual(self.account(self.alice)["inventory"], {REGULAR: 1})

    async def test_tickets_can_be_bought_with_the_corrected_prices(self):
        self.fund(self.alice, 1100)
        await self.run_command("buy", self.alice, item="regular lottery ticket", quantity=1)
        await self.run_command("buy", self.alice, item="golden lottery ticket", quantity=1)
        account = self.account(self.alice)
        self.assertEqual((account["wallet"], account["inventory"]), (0, {REGULAR: 1, GOLDEN: 1}))


class LotteryInfoTests(CogTestCase):
    async def test_info_card_lists_odds_prices_and_your_tickets(self):
        self.fund(self.alice, 0, inventory={REGULAR: 3})
        it = await self.run_command("lottery", self.alice)
        text = it.text()
        for expected in ("1 in 50,000", "1 in 500,000", "1000000", "100000000", "Regular Ticket", "Golden Ticket",
                         "Regular: **3**", "Golden: **0**", "64.00%"):
            self.assertIn(expected, text)

    async def test_info_card_shows_recent_jackpot_winners(self):
        self.fund(self.alice, 0, inventory={REGULAR: 1})
        self.script.rolls += [JACKPOT_ROLL]
        await self.run_command("use", self.alice, item="regular lottery ticket")
        it = await self.run_command("lottery", self.bob)
        self.assertIn("Recent jackpots", it.text())
        self.assertIn("<@1001> won", it.text())


class BirthdayCommandTests(CogTestCase):
    def march(self):
        return app_commands.Choice(name="March", value=3)

    async def test_set_view_and_remove(self):
        it = await self.run_command("birthday", self.alice, group="set", day=15, month=self.march(), year=2000)
        self.assertIn("15 March 2000", it.text())
        it = await self.run_command("birthday", self.alice, group="view", user=None)
        self.assertIn("today", it.text())
        self.assertIn("Turning **26**", it.text())
        it = await self.run_command("birthday", self.alice, group="remove")
        self.assertIn("removed", it.text())
        it = await self.run_command("birthday", self.alice, group="view", user=None)
        self.assertIn("haven't set", it.text())
        self.assertEqual(it.ephemeral(), [True])

    async def test_impossible_dates_are_refused(self):
        feb = app_commands.Choice(name="February", value=2)
        it = await self.run_command("birthday", self.alice, group="set", day=30, month=feb, year=None)
        self.assertIn("does not exist", it.text())
        self.assertEqual(it.ephemeral(), [True])
        self.assertIsNone(self.s.birthdays.get_birthday(self.alice.id))

    async def test_viewing_someone_without_a_birthday(self):
        it = await self.run_command("birthday", self.alice, group="view", user=self.bob)
        self.assertIn("bob hasn't set", it.text())

    async def test_upcoming_is_ordered_by_days_left(self):
        for user, (month, day) in ((self.alice, (12, 25)), (self.bob, (3, 16))):
            await self.run_command("birthday", user, group="set", day=day, month=app_commands.Choice(name="x", value=month), year=None)
        text = (await self.run_command("birthday", self.alice, group="upcoming")).text()
        self.assertLess(text.index("<@1002>"), text.index("<@1001>"))
        self.assertIn("tomorrow", text)


class BirthdayJobTests(CogTestCase):
    async def test_the_greeting_job_gifts_and_announces_once_a_year(self):
        self.s.birthdays.set_birthday(self.alice.id, 3, 15, 2000)
        cog = self.cog("BirthdayCog")
        await cog.greet()
        self.assertEqual(self.account(self.alice)["wallet"], 500)
        (call,) = self.s.messenger.post.await_args_list
        self.assertEqual(call.args[0], self.s.settings.channels.birthdays)
        self.assertIn("Happy Birthday", call.kwargs["embed"].title)
        self.assertIn("Turning **26**", call.kwargs["embed"].description)
        await cog.greet()                                              # the job runs every 10 minutes: no repeat gift
        self.assertEqual(self.account(self.alice)["wallet"], 500)
        self.assertEqual(self.s.messenger.post.await_count, 1)

    async def test_cake_is_delivered_when_due_and_survives_in_the_log(self):
        self.fund(self.alice, 0, inventory={CAKE_ITEM: 1})
        self.s.birthdays.schedule_cake(owner_id=self.alice.id, recipient_id=self.bob.id, date_text="16/03/2026",
                                       time_text="09:30", message_text="Cheers!")
        cog = self.cog("BirthdayCog")
        await cog.deliver_cakes()
        self.s.messenger.post.assert_not_called()                      # not yet due
        self.clock.advance(days=1)
        await cog.deliver_cakes()
        (call,) = self.s.messenger.post.await_args_list
        self.assertIn("Cheers!", call.kwargs["embed"].description)
        self.assertEqual(self.s.birthdays.due_cake_celebrations(), [])
        self.assertEqual([e["status"] for e in self.s.birthdays._celebrations.all()], ["sent"])

    async def test_a_failing_delivery_is_retried_then_given_up(self):
        self.fund(self.alice, 0, inventory={CAKE_ITEM: 1})
        self.s.birthdays.schedule_cake(owner_id=self.alice.id, recipient_id=self.bob.id, date_text="16/03/2026",
                                       time_text="", message_text="")
        self.clock.advance(days=2)
        self.s.messenger.post.return_value = False
        cog = self.cog("BirthdayCog")
        statuses = []
        with self.assertLogs("rudhabot.birthdays", level="WARNING"):             # logged once it is given up on
            for _ in range(4):
                await cog.deliver_cakes()
                statuses.append(self.s.birthdays._celebrations.all()[0]["status"])
        self.assertEqual(statuses, ["pending", "pending", "failed", "failed"])
        self.assertEqual(self.s.messenger.post.await_count, 3)         # three attempts, then it stops

    async def test_cake_modal_schedules_and_confirms(self):
        from rudhabot.views.birthday import BirthdayCakeModal
        self.fund(self.alice, 0, inventory={CAKE_ITEM: 1})
        modal = BirthdayCakeModal(self.s, self.alice)
        modal.date._value, modal.time._value = "20/03/2026", "14:30"
        modal.to._value, modal.message._value = "", "Happy day"
        from tests.fakes import FakeInteraction
        it = FakeInteraction(self.bot, self.alice)
        await modal.on_submit(it)
        self.assertIn("scheduled", it.text())
        self.assertIn("14:30 Rudhanese Time", it.text())
        self.assertEqual(self.account(self.alice)["inventory"], {})
        self.assertEqual(len(self.s.birthdays._celebrations.all()), 1)
        bad = FakeInteraction(self.bot, self.alice)
        modal.date._value = "31/02/2026"
        await modal.on_submit(bad)
        self.assertIn("Invalid date", bad.text())

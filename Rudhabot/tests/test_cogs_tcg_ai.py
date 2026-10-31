"""Command-level tests for /tcg, /ask and /oracle through the real bot, cogs and services (no network)."""
import discord

from rudhabot.tcg import engine as E
from rudhabot.views.help import get_commands_data
from rudhabot.views.tcg_match import MovePicker
from tests.cog_case import CogTestCase
from tests.fakes import FakeChannel, FakeInteraction


class TcgCogTests(CogTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel = FakeChannel()
        self.bot.get_channel = lambda cid: self.channel if cid == self.channel.id else None

    async def invoke(self, path, actor, **kwargs):
        """Run `/tcg practice` style commands (path = "tcg practice" / "tcg deck analyze")."""
        parts = path.split()
        command = self.bot.tree.get_command(parts[0])
        for part in parts[1:]:
            command = command.get_command(part)
        it = FakeInteraction(self.bot, actor)
        it.channel = self.channel
        it.command = command
        try:
            await command._check_can_run(it)
            await command.callback(command.binding, it, **kwargs)
        except discord.app_commands.AppCommandError as error:
            await self.bot.tree.on_error(it, error)
        return it

    def choice(self, value):
        return discord.app_commands.Choice(name=value, value=value)

    async def test_starter_then_refuse_a_second_one(self):
        it = await self.invoke("tcg starter", self.alice, deck=self.choice("storm"))
        self.assertIn("Stormcallers", it.text())
        again = await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        self.assertIn("already claimed", again.text())
        self.assertEqual(again.ephemeral(), [True])

    async def test_card_lookup_shows_text_and_oracle_neighbours(self):
        it = await self.invoke("tcg card", self.alice, name="Wahen")
        self.assertIn("Valleyflow", it.text())
        self.assertIn("Oracle", it.text())

    async def test_practice_match_full_flow_through_buttons_and_picker(self):
        await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        await self.invoke("tcg practice", self.alice, difficulty=self.choice("hard"), opponent=self.choice("storm"))
        board = self.channel.sent[0]
        embed = board.content["embed"]
        self.assertIn("Cloudfall", embed.title)
        self.assertIn("🔮", embed.description)                                   # spectator-safe win bar
        cog = self.cog("TcgCog")
        match = self.s.tcg.match_for(self.alice.id)
        # a spectator can look but not act
        spectator = FakeInteraction(self.bot, self.bob)
        await cog.on_board_button(spectator, "act", match.id)
        self.assertIn("watching", spectator.text())
        moves = 0
        while not match.game.over and moves < 40:
            press = FakeInteraction(self.bot, self.alice)
            await cog.on_board_button(press, "act", match.id)
            picker = press.log[-1][1].get("view")
            self.assertIsInstance(picker, MovePicker)
            act = next((a for a in picker.acts if a.key in ("clash", "done", "noblock") or a.key.endswith(":keep")
                        or a.key.endswith(":pass") or a.key.endswith(":skip")), picker.acts[0])
            pick = FakeInteraction(self.bot, self.alice)
            await cog.on_move(pick, match.id, act.key, act.label, picker.version)
            self.assertIn("✅", pick.text())
            moves += 1
        self.assertGreater(len(match.record["keys"]), moves)                     # the Oracle moved too
        self.assertTrue(board.edits)                                             # the public board was refreshed

    async def test_stale_menus_are_refused(self):
        await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        await self.invoke("tcg practice", self.alice, difficulty=self.choice("easy"))
        cog = self.cog("TcgCog")
        match = self.s.tcg.match_for(self.alice.id)
        old_version = match.version
        act = E.legal_actions(match.game)[0]
        await cog.on_move(FakeInteraction(self.bot, self.alice), match.id, act.key, act.label, old_version)
        stale = FakeInteraction(self.bot, self.alice)
        await cog.on_move(stale, match.id, act.key, act.label, old_version)
        self.assertIn("out of date", stale.text())

    async def test_hand_is_private_and_hints_need_coaching(self):
        await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        await self.invoke("tcg starter", self.bob, deck=self.choice("storm"))
        match = self.s.tcg.create_pvp(self.alice.id, "alice", self.bob.id, "bob")
        cog = self.cog("TcgCog")
        hand = FakeInteraction(self.bot, self.alice)
        await cog.on_board_button(hand, "hand", match.id)
        self.assertEqual(hand.ephemeral(), [True])
        decider = self.alice if match.seat_of(self.alice.id) == E.decider(match.game) else self.bob
        hint = FakeInteraction(self.bot, decider)
        await cog.on_board_button(hint, "hint", match.id)
        self.assertIn("Coaching is off", hint.text())

    async def test_forfeit_announces_the_result(self):
        await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        await self.invoke("tcg practice", self.alice)
        it = await self.invoke("tcg forfeit", self.alice)
        self.assertIn("conceded", it.text())
        self.assertIsNone(self.s.tcg.match_for(self.alice.id))
        self.assertTrue(any("forfeited" in (m.content.get("content") or "") for m in self.channel.sent))


class AssistantAndOracleCogTests(CogTestCase):
    async def test_ask_without_openai_uses_local_docs_and_discloses_it(self):
        it = await self.run_command("ask", self.alice, question="how do loans work?")
        footer = it.last_embed.footer.text
        self.assertIn("built-in docs", footer)
        self.assertIn("loan", it.text().lower())

    async def test_oracle_status_is_admin_only(self):
        command = self.bot.tree.get_command("oracle").get_command("status")
        it = FakeInteraction(self.bot, self.alice)
        it.command = command
        try:
            await command._check_can_run(it)
        except discord.app_commands.AppCommandError as error:
            await self.bot.tree.on_error(it, error)
        self.assertTrue(it.log)                                                  # refused with the standard message
        admin = FakeInteraction(self.bot, self.admin)
        admin.command = command
        await command._check_can_run(admin)
        await command.callback(command.binding, admin)
        self.assertIn("oracle-v1", admin.text())

    def test_help_lists_the_new_features_and_the_true_daily_amount(self):
        names = [c["name"] for c in get_commands_data()]
        self.assertIn("ask", names)
        self.assertIn("tcg practice", names)
        daily = next(c for c in get_commands_data() if c["name"] == "daily")
        self.assertIn("50ra", daily["description"])

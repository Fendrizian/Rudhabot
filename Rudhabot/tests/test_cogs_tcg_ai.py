"""Command-level tests for /tcg, /ask and /clairvoyance through the real bot, cogs and services (no network)."""
import discord

from rudhabot.tcg import engine as E
from rudhabot.views.help import get_commands_data
from rudhabot.views.tcg_match import MovePicker
from rudhabot.ui.theme import CLAIRVOYANCEICON
from tests.cog_case import CogTestCase
from tests.fakes import FakeChannel, FakeInteraction


class TcgCogBase(CogTestCase):
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


class TcgCogTests(TcgCogBase):
    async def test_starter_then_refuse_a_second_one(self):
        it = await self.invoke("tcg starter", self.alice, deck=self.choice("storm"))
        self.assertIn("Stormcallers", it.text())
        again = await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        self.assertIn("already claimed", again.text())
        self.assertEqual(again.ephemeral(), [True])

    async def test_card_lookup_shows_text_and_clairvoyance_neighbours(self):
        it = await self.invoke("tcg card", self.alice, name="Wahen")
        self.assertIn("Valleyflow", it.text())
        self.assertIn("Clairvoyance", it.text())

    async def test_practice_match_full_flow_through_buttons_and_picker(self):
        await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        await self.invoke("tcg practice", self.alice, difficulty=self.choice("hard"), opponent=self.choice("storm"))
        board = self.channel.sent[0]
        embed = board.content["embed"]
        self.assertIn("Cloudfall", embed.title)
        self.assertIn(CLAIRVOYANCEICON, embed.description)                                   # spectator-safe win bar
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
        self.assertGreater(len(match.record["keys"]), moves)                     # the Clairvoyance moved too
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


def forbidden():
    from types import SimpleNamespace
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), {"code": 50013, "message": "Missing Permissions"})


class BoardPermissionTests(TcgCogBase):
    """The /tcg practice 403: the bot may lack Send Messages / Embed Links / thread permissions in a channel."""

    async def test_board_falls_back_to_the_reply_when_the_channel_is_forbidden(self):
        async def refuse(*a, **k):
            raise forbidden()
        self.channel.send = refuse
        self.channel.get_partial_message = lambda mid: (_ for _ in ()).throw(forbidden())
        await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        it = await self.invoke("tcg practice", self.alice)
        match = self.s.tcg.match_for(self.alice.id)
        self.assertIsNotNone(match)
        self.assertEqual(match.record["board_mode"], "reply")
        self.assertTrue(any("Cloudfall" in (k.get("embed").title or "") for _, k in it.log if k.get("embed")))
        self.assertIn("can't post in this channel", " ".join(str(k.get("content")) for _, k in it.log))
        # moves still work, and the player is shown the current board privately
        cog = self.cog("TcgCog")
        act = next(a for a in E.legal_actions(match.game))
        pick = FakeInteraction(self.bot, self.alice)
        await cog.on_move(pick, match.id, act.key, act.label, match.version)
        self.assertTrue(any(k.get("embeds") for _, k in pick.log))

    async def test_a_board_that_cannot_be_shown_anywhere_cancels_the_match(self):
        async def refuse(*a, **k):
            raise forbidden()
        self.channel.send = refuse
        await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        cog = self.cog("TcgCog")
        match = self.s.tcg.create_practice(self.alice.id, "alice")
        it = FakeInteraction(self.bot, self.alice)
        it.channel = self.channel
        it.followup.send = refuse
        self.assertFalse(await cog._post_board(it, match, "title"))
        self.assertIsNone(self.s.tcg.match_for(self.alice.id))             # not stuck in an invisible match

    async def test_pressing_act_on_the_board_refreshes_it(self):
        await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        await self.invoke("tcg practice", self.alice)
        match = self.s.tcg.match_for(self.alice.id)
        press = FakeInteraction(self.bot, self.alice)
        press.message = self.channel.sent[0]
        await self.cog("TcgCog").on_board_button(press, "act", match.id)
        kinds = [kind for kind, _ in press.log]
        self.assertEqual(kinds[:2], ["edit_message", "followup"])
        self.assertIsInstance(press.log[1][1]["view"], MovePicker)


class AssistantAndClairvoyanceCogTests(CogTestCase):
    async def test_ask_without_openai_uses_local_docs_and_discloses_it(self):
        it = await self.run_command("ask", self.alice, question="what's the capital of france")
        footer = it.last_embed.footer.text
        self.assertIn("built-in docs", footer)
        self.assertIn("AI answers are off", it.text())

    async def test_clairvoyance_status_is_admin_only(self):
        command = self.bot.tree.get_command("clairvoyance").get_command("status")
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
        self.assertIn(self.s.clairvoyance.version, admin.text())                 # the served model is named

    def test_help_lists_the_new_features_and_the_true_daily_amount(self):
        names = [c["name"] for c in get_commands_data()]
        self.assertIn("ask", names)
        self.assertIn("tcg practice", names)
        daily = next(c for c in get_commands_data() if c["name"] == "daily")
        self.assertIn("50ra", daily["description"])


class ComponentPayloadTests(CogTestCase):
    def test_board_button_emojis_are_standard_discord_emojis(self):
        # Discord rejects the whole message (400 "Invalid emoji") if one button uses a character that isn't a standard
        # emoji; 🂠 (playing card back) did exactly that. Keep button emojis to this vetted set.
        from rudhabot.views.tcg_match import MatchButton, board_view
        vetted = {"⚡", "🃏", "🔮", "📜"}
        self.assertTrue({emoji for _, emoji, _ in MatchButton.LABELS.values()} <= vetted)
        for item in board_view("0123456789", coach=True).children:
            self.assertIn(str(item.item.emoji), vetted)

    async def test_failed_thread_post_deletes_the_empty_thread(self):
        from types import SimpleNamespace
        from unittest import mock
        cog = self.bot.cogs["TcgCog"]
        self.s.tcg.claim_starter(self.alice.id, "vanguard")
        match = self.s.tcg.create_practice(self.alice.id, "alice")
        bad = discord.HTTPException(SimpleNamespace(status=400, reason="Bad Request"), {"code": 50035, "message": "Invalid Form Body"})
        thread = mock.AsyncMock()
        thread.send.side_effect = bad
        channel = mock.MagicMock(spec=discord.TextChannel)
        channel.id = 777
        channel.create_thread = mock.AsyncMock(return_value=thread)
        channel.send = mock.AsyncMock(side_effect=bad)
        it = FakeInteraction(self.bot, self.alice)
        it.channel = channel
        self.assertTrue(await cog._post_board(it, match, "title"))       # fell back to the reply
        thread.delete.assert_awaited_once()


class ThreadNameTests(TcgCogBase):
    def test_thread_names_never_carry_custom_emojis(self):
        from rudhabot.cogs.tcg import thread_name
        self.assertEqual(thread_name(f"{CLAIRVOYANCEICON} alice vs the Clairvoyance"), "alice vs the Clairvoyance")
        self.assertEqual(thread_name("🔮 alice vs the Clairvoyance"), "🔮 alice vs the Clairvoyance")
        self.assertEqual(thread_name("<a:spin:123>"), "Cloudfall match")
        self.assertLessEqual(len(thread_name("x" * 200)), 90)

    async def test_practice_thread_is_named_with_a_unicode_emoji(self):
        from types import SimpleNamespace
        from unittest import mock
        cog = self.bot.cogs["TcgCog"]
        self.s.tcg.claim_starter(self.alice.id, "vanguard")
        match = self.s.tcg.create_practice(self.alice.id, "alice")
        bad = discord.HTTPException(SimpleNamespace(status=400, reason="Bad Request"), {"code": 50035, "message": "x"})
        thread = mock.AsyncMock()
        thread.send.side_effect = bad                      # only the name matters here; the post falls back
        channel = mock.MagicMock(spec=discord.TextChannel)
        channel.id = 777
        channel.create_thread = mock.AsyncMock(return_value=thread)
        channel.send = mock.AsyncMock(side_effect=bad)
        it = FakeInteraction(self.bot, self.alice)
        it.channel = channel
        await cog._post_board(it, match, f"{CLAIRVOYANCEICON} alice vs the Clairvoyance")
        name = channel.create_thread.await_args.kwargs["name"]
        self.assertNotIn("<:", name)


class MenuLifetimeTests(TcgCogBase):
    """The move menu used to stop responding after 3 minutes (and on every restart)."""

    async def test_move_menu_never_times_out_and_works_from_its_custom_id(self):
        from rudhabot.views.tcg_match import PickSelect
        await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        await self.invoke("tcg practice", self.alice)
        match = self.s.tcg.match_for(self.alice.id)
        cog = self.cog("TcgCog")
        picker = MovePicker(cog, match.id, match.version, E.legal_actions(match.game))
        self.assertIsNone(picker.timeout)
        select = picker.children[0]
        self.assertIsInstance(select, PickSelect)
        # simulate a click arriving much later, e.g. after a restart: only the custom id is known
        self.clock.advance(hours=2)
        restored = await PickSelect.from_custom_id(None, select.item, PickSelect.__discord_ui_compiled_template__.match(select.item.custom_id))
        before = match.version
        it = FakeInteraction(self.bot, self.alice)
        it.data = {"values": ["0"]}
        await restored.callback(it)
        self.assertGreater(match.version, before)
        stale = FakeInteraction(self.bot, self.alice)
        stale.data = {"values": ["0"]}
        await restored.callback(stale)                                   # same old menu again: refused
        self.assertIn("out of date", stale.text())

    async def test_paging_rebuilds_the_menu_and_refuses_stale_pages(self):
        await self.invoke("tcg starter", self.alice, deck=self.choice("vanguard"))
        await self.invoke("tcg practice", self.alice)
        match = self.s.tcg.match_for(self.alice.id)
        cog = self.cog("TcgCog")
        ok = FakeInteraction(self.bot, self.alice)
        await cog.on_page(ok, match.id, match.version, 0)
        self.assertIsInstance(ok.log[-1][1]["view"], MovePicker)
        stale = FakeInteraction(self.bot, self.alice)
        await cog.on_page(stale, match.id, match.version - 1, 0)
        self.assertIn("out of date", stale.text())

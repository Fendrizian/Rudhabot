"""/guide: the guidebook asset parses into chapters that fit Discord, each reader sees only their chapters, the command
is private unless a member chapter is posted publicly, navigation never expires, and /ask respects the same rules."""
import os
import unittest
from types import SimpleNamespace

from rudhabot.services.permissions import EVERYTHING, MEMBER, REQUIREMENTS, Viewer
from rudhabot.views.guide import (GUIDE_FILE, ChapterSelect, GuideButton, chapters, hidden_chapter_ids, paginate,
                                  parse, render, visible_chapters)
from tests.cog_case import CogTestCase
from tests.fakes import FakeInteraction

STAFF = {"who_sees_which_commands", "bankers", "moderators", "security", "admins"}


def keys(viewer):
    return [c.key for c in visible_chapters(viewer)]


class GuidebookAssetTests(unittest.TestCase):
    def test_the_asset_parses_into_fourteen_chapters_that_fit_discord(self):
        found = chapters()
        self.assertEqual(len(found), 14)
        self.assertEqual(found[0].key, "getting_started")
        self.assertEqual(found[-1].key, "admins")
        self.assertEqual({c.part for c in found}, {"Part 1: For every member", "Part 2: For staff"})
        for c in found:
            self.assertTrue(c.text, c.key)
            self.assertTrue(all(0 < len(p) <= 4096 for p in c.pages), c.key)
            self.assertTrue(c.requires is None or c.requires in REQUIREMENTS, c.key)
            self.assertLessEqual(len(c.summary), 100)
        self.assertEqual({c.key for c in found if c.requires}, STAFF)

    def test_no_tables_or_editor_notes_reach_discord(self):
        with open(GUIDE_FILE, encoding="utf-8") as handle:
            raw = handle.read()
        self.assertFalse([line for line in raw.splitlines() if line.lstrip().startswith("|")])   # embeds can't draw tables
        self.assertTrue(all("<!--" not in p for c in chapters() for p in c.pages))

    def test_parsing_and_paging_rules(self):
        lead, found = parse("# T\n\nLead text.\n\n## Part A\n\n### One\n\nHello.\n\n### Two {admin}\n\nSecret.\n")
        self.assertEqual(lead, "Lead text.")
        self.assertEqual([(c.key, c.requires, c.text) for c in found], [("one", None, "Hello."), ("two", "admin", "Secret.")])
        with self.assertRaises(ValueError):
            parse("## P\n\n### Bad {wizard}\n\nx")
        pages = paginate("\n\n".join(["a" * 1000] * 9), limit=3800)
        self.assertEqual(len(pages), 3)
        self.assertTrue(all(len(p) <= 3800 for p in pages))
        self.assertTrue(all(len(p) <= 100 for p in paginate("x" * 50 + "\n" + "y" * 350, limit=100)))


class GuideVisibilityTests(unittest.TestCase):
    def test_each_reader_sees_their_own_chapters(self):
        self.assertFalse(set(keys(MEMBER)) & STAFF)
        self.assertEqual(len(keys(MEMBER)), 9)
        banker = set(keys(Viewer(banker=True)))
        self.assertTrue({"who_sees_which_commands", "bankers"} <= banker)
        self.assertFalse(banker & {"moderators", "security", "admins"})
        helper = set(keys(Viewer(tier=1)))
        self.assertTrue({"who_sees_which_commands", "moderators", "security"} <= helper)
        self.assertFalse(helper & {"bankers", "admins"})
        self.assertEqual(len(keys(EVERYTHING)), 14)

    def test_hidden_chapters_fall_back_to_the_contents(self):
        embed, view = render("admins", viewer=MEMBER)
        self.assertIn("Rudhabot Guidebook", embed.title)
        self.assertNotIn("Part 2: For staff", [f.name for f in embed.fields])
        menu = next(i for i in view.children if isinstance(i, ChapterSelect))
        self.assertFalse({o.value for o in menu.item.options} & STAFF)
        self.assertEqual(hidden_chapter_ids(EVERYTHING), set())
        self.assertIn("guide:admins", hidden_chapter_ids(MEMBER))

    def test_next_walks_every_page_in_order_and_ends(self):
        embed, view = render("home", viewer=MEMBER)
        seen = []
        target, page = "home", 0
        for _ in range(40):
            nxt = next(i for i in view.children if isinstance(i, GuideButton) and i.item.label == "Next ▶")
            if nxt.item.disabled:
                break
            target, page = nxt.target, nxt.page
            embed, view = render(target, page, MEMBER)
            seen.append(target)
        self.assertEqual(seen, keys(MEMBER))                         # one page each, member chapters only
        ids = [i.item.custom_id for i in view.children]
        self.assertEqual(len(ids), len(set(ids)))                     # unique custom ids, even at the end


class GuideCommandTests(CogTestCase):
    async def test_private_by_default_and_public_for_member_chapters(self):
        it = await self.run_command("guide", self.alice)
        self.assertTrue(it.log[0][1]["ephemeral"])
        self.assertIn("Rudhabot Guidebook", it.last_embed.title)
        it = await self.run_command("guide", self.alice, chapter_key="earning_money", public=True)
        self.assertNotIn("ephemeral", it.log[0][1])
        self.assertIn("Earning money", it.last_embed.title)
        self.assertIn("/collect", it.last_embed.description)

    async def test_staff_chapters_stay_private_and_public_posts_show_member_content(self):
        it = await self.run_command("guide", self.admin, chapter_key="admins", public=True)
        self.assertTrue(it.log[0][1]["ephemeral"])
        self.assertIn("only shown privately", it.last_embed.footer.text)
        it = await self.run_command("guide", self.admin, public=True)            # the contents, posted publicly
        self.assertNotIn("Part 2: For staff", [f.name for f in it.last_embed.fields])
        it = await self.run_command("guide", self.alice, chapter_key="admins")
        self.assertNotIn("Admins", it.last_embed.title)                          # members can't open it

    async def test_autocomplete_and_navigation_from_custom_ids(self):
        cog = self.cog("GuideCog")
        member = [c.value for c in await cog._chapters(FakeInteraction(self.bot, self.alice), "")]
        self.assertIn("getting_started", member)
        self.assertFalse(set(member) & STAFF)
        admin = [c.value for c in await cog._chapters(FakeInteraction(self.bot, self.admin), "bank")]
        self.assertEqual(admin, ["bank_cards_and_loans", "bankers"])
        button = GuideButton("markets", 0)
        rebuilt = await GuideButton.from_custom_id(None, button.item,
                                                   GuideButton.__discord_ui_compiled_template__.match(button.item.custom_id))
        it = FakeInteraction(self.bot, self.alice)
        await rebuilt.callback(it)
        self.assertEqual(it.log[0][0], "edit_message")
        self.assertIn("Markets", it.last_embed.title)

    async def test_someone_elses_click_gets_their_own_private_copy(self):
        it = FakeInteraction(self.bot, self.alice)
        it.data = {"values": ["admins"]}
        it.message = SimpleNamespace(interaction_metadata=SimpleNamespace(user=SimpleNamespace(id=self.admin.id)),
                                     flags=SimpleNamespace(ephemeral=False))
        await ChapterSelect().callback(it)
        self.assertEqual(it.log[0][0], "send")
        self.assertTrue(it.log[0][1]["ephemeral"])
        self.assertNotIn("Admins", it.last_embed.title)

    async def test_ask_quotes_the_guidebook_but_never_a_staff_chapter_to_members(self):
        assistant = self.s.assistant
        from rudhabot.views.help import hidden_commands
        hidden = frozenset(hidden_commands(MEMBER) | hidden_chapter_ids(MEMBER))
        answer = assistant.local_answer("why can't I send money during a raid alert", hidden=hidden)
        self.assertIn("/guide", answer.commands)
        staff = assistant.local_answer("what is the False alarm button", hidden=hidden)
        self.assertNotIn("Guidebook: Security", staff.text)
        admin = assistant.local_answer("what is the False alarm button")
        self.assertIn("Guidebook: Security", admin.text)

    async def test_the_guide_is_documented_in_help(self):
        from rudhabot.views.help import get_commands_data
        self.assertIn("guide", {c["name"] for c in get_commands_data()})
        self.assertTrue(os.path.exists(GUIDE_FILE))

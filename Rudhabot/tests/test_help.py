"""/help: sectioned overview, single sections, the full list, and never-expiring navigation."""
from types import SimpleNamespace

import discord

from rudhabot.services.permissions import EVERYTHING, MEMBER, Viewer
from rudhabot.views.help import (REQUIRES, SECTIONS, NavButton, SectionSelect, full_pages, get_commands_data,
                                 hidden_commands, ordered_commands, render, visible_sections)

ALL = EVERYTHING
from tests.cog_case import CogTestCase
from tests.fakes import FakeInteraction


class HelpDataTests(CogTestCase):
    def test_every_command_is_in_exactly_one_section(self):
        names = [c["name"] for c in get_commands_data()]
        placed = [n for *_, section in SECTIONS for n in section]
        self.assertEqual(sorted(placed), sorted(names))

    def test_every_registered_slash_command_is_documented(self):
        documented = {c["name"] if c["name"][:1].isupper() else c["name"].split()[0] for c in get_commands_data()}
        registered = {c.name for c in self.bot.tree.get_commands()}              # includes context menus
        self.assertEqual(registered - documented, set())

    def test_full_list_shows_every_command_once_alphabetically(self):
        seen = []
        for page in range(full_pages(ALL)):
            embed, _ = render("full", page, ALL)
            seen += [f.name for f in embed.fields]
        self.assertEqual(len(seen), len(get_commands_data()))
        self.assertEqual(len(set(seen)), len(seen))
        self.assertEqual([c["name"] for c in ordered_commands(ALL)],
                         sorted((c["name"] for c in get_commands_data()), key=str.casefold))

    def test_every_view_lists_commands_alphabetically(self):
        # house rule: alphabetical in every section, in the full list and in the overview samples
        for key, *_ in SECTIONS:
            names = [f.name.lstrip("/").split(" <")[0].split(" [")[0] for f in render(key, viewer=ALL)[0].fields]
            self.assertEqual(names, sorted(names, key=str.casefold), key)
        seen = []
        for page in range(full_pages(ALL)):
            seen += [f.name.lstrip("/") for f in render("full", page, ALL)[0].fields]
        self.assertEqual(seen, sorted(seen, key=str.casefold))
        for field in render("home", viewer=ALL)[0].fields:
            samples = __import__("re").findall(r"`/([^`]+)`", field.value)
            self.assertTrue(all(a.casefold() <= b.casefold() for a, b in zip(samples, samples[1:])), field.name)

    def test_long_sections_are_split_into_pages_covering_every_command_once(self):
        from rudhabot.views.help import PER_PAGE, section_pages
        for key, *_, names in SECTIONS:
            seen = []
            for page in range(section_pages(key, ALL)):
                fields = render(key, page, ALL)[0].fields
                self.assertLessEqual(len(fields), PER_PAGE, key)
                seen += [f.name for f in fields]
            self.assertEqual(len(seen), len(names), key)
            self.assertEqual(len(set(seen)), len(seen), key)
        self.assertGreater(section_pages("security", ALL), 1)
        embed, view = render("security", 1, ALL)
        self.assertIn("(2/", embed.title)
        self.assertTrue(any(isinstance(i, NavButton) and i.target == "security" for i in view.children))
        ids = [i.item.custom_id for i in render("security", section_pages("security", ALL) - 1, ALL)[1].children]
        self.assertEqual(len(ids), len(set(ids)))                          # unique custom ids even on the last page

    def test_only_the_main_page_embeds_the_dri_banner(self):
        from rudhabot.ui.theme import BOTICON
        from rudhabot.views.help import HELP_IMAGE
        self.assertTrue(HELP_IMAGE.endswith("dri.png"))
        self.assertEqual(render("home", viewer=ALL)[0].image.url, "attachment://dri.png")
        for target in ["full"] + [k for k, *_ in SECTIONS]:
            self.assertIsNone(render(target, viewer=ALL)[0].image.url, target)
        self.assertTrue(render("home", viewer=ALL)[0].title.startswith(BOTICON))
        self.assertIn("Copyright 2026.", render("home", viewer=ALL)[0].description)


class HelpCommandTests(CogTestCase):
    async def test_plain_help_shows_the_section_overview_with_navigation(self):
        it = await self.run_command("help", self.alice)
        embed, view = it.log[0][1]["embed"], it.log[0][1]["view"]
        self.assertEqual(it.log[0][1]["file"].filename, "dri.png")        # the emblem is uploaded with the message
        self.assertEqual(len(embed.fields), len(visible_sections(MEMBER)))      # a member's own sections only
        self.assertFalse(it.log[0][1]["ephemeral"])                              # members keep the public reply
        self.assertIn("Economy", it.text())
        self.assertIsNone(view.timeout)                                  # navigation never expires
        kinds = [type(item) for item in view.children]
        self.assertIn(SectionSelect, kinds)
        self.assertIn(NavButton, kinds)

    async def test_help_with_a_section_jumps_straight_to_it(self):
        section = discord.app_commands.Choice(name="Bank", value="bank")
        it = await self.run_command("help", self.alice, section=section)
        self.assertIn("/loan <amount> <days>", it.text())
        self.assertNotIn("/slots", it.text())
        self.assertNotIn("file", it.log[0][1])                             # no banner on a section page
        full = await self.run_command("help", self.alice, section=discord.app_commands.Choice(name="Full", value="full"))
        self.assertIn(f"Page 1/{full_pages(MEMBER)}", full.last_embed.footer.text)

    async def test_menu_switches_sections_for_the_owner_and_gives_others_a_private_copy(self):
        def click(user, owner_id):
            it = FakeInteraction(self.bot, user)
            it.data = {"values": ["games"]}
            it.message = SimpleNamespace(interaction_metadata=SimpleNamespace(user=SimpleNamespace(id=owner_id)),
                                         flags=SimpleNamespace(ephemeral=False))
            return it
        owner = click(self.alice, self.alice.id)
        await SectionSelect().callback(owner)
        self.assertEqual(owner.log[0][0], "edit_message")
        self.assertIn("/blackjack", owner.text())
        self.assertEqual(owner.log[0][1]["attachments"], [])               # leaving the main page drops the banner
        other = click(self.bob, self.alice.id)
        await SectionSelect().callback(other)
        self.assertEqual(other.log[0][0], "send")
        self.assertTrue(other.log[0][1]["ephemeral"])
        self.assertNotIn("file", other.log[0][1])                          # a section copy has no banner

    async def test_navigation_works_from_custom_ids_alone(self):
        # e.g. after a restart: discord.py rebuilds the item from its id
        button = NavButton("full", 2)
        rebuilt = await NavButton.from_custom_id(None, button.item, NavButton.__discord_ui_compiled_template__.match(button.item.custom_id))
        it = FakeInteraction(self.bot, self.alice)
        await rebuilt.callback(it)
        self.assertIn(f"Page 3/{full_pages(MEMBER)}", it.last_embed.footer.text)
        self.assertEqual(it.log[0][1]["attachments"], [])
        home = FakeInteraction(self.bot, self.alice)
        await NavButton("home", 0).callback(home)
        self.assertEqual(len(home.last_embed.fields), len(visible_sections(MEMBER)))
        self.assertEqual([a.filename for a in home.log[0][1]["attachments"]], ["dri.png"])   # back home: banner returns


STAFF_SECTIONS = ("banker", "admin", "moderation", "security")


def section_keys(viewer):
    return {k for k, *_ in visible_sections(viewer)}


class HelpVisibilityTests(CogTestCase):
    def test_every_staff_entry_needs_a_rank(self):
        for key, *_, names in SECTIONS:
            if key in STAFF_SECTIONS:
                for name in names:
                    if name != "Report to moderators":              # anyone can report a message
                        self.assertIn(name, REQUIRES, f"{name} would be shown to every member")
        documented = {c["name"] for c in get_commands_data()}
        self.assertEqual(set(REQUIRES) - documented - {"set-income"}, set())   # no stale entries

    def test_members_see_only_their_own_sections_and_commands(self):
        self.assertFalse(section_keys(MEMBER) & {"banker", "admin", "security"})
        member_moderation = [f.name for f in render("moderation", viewer=MEMBER)[0].fields]
        self.assertEqual(member_moderation, ["Report to moderators"])            # the one moderation entry for all
        names = {c["name"] for c in ordered_commands(MEMBER)}
        self.assertTrue({"balance", "help", "work", "Report to moderators"} <= names)
        self.assertFalse(names & {"add-money", "audit", "ban", "warn", "security status", "setup"})
        embed, view = render("admin", viewer=MEMBER)                             # a hidden section: the overview
        self.assertEqual(embed.image.url, "attachment://dri.png")
        menu = next(i for i in view.children if isinstance(i, SectionSelect))
        self.assertFalse({o.value for o in menu.item.options} & {"banker", "admin", "security"})

    def test_each_rank_sees_what_it_can_use(self):
        banker = Viewer(banker=True)
        self.assertIn("banker", section_keys(banker))
        self.assertNotIn("admin", section_keys(banker))
        helper = Viewer(tier=1)
        helper_names = {c["name"] for c in ordered_commands(helper)}
        self.assertTrue({"warn", "modlog", "security status"} <= helper_names)
        self.assertFalse(helper_names & {"kick", "ban", "security level", "audit", "add-money"})
        senior = {c["name"] for c in ordered_commands(Viewer(tier=3))}
        self.assertTrue({"ban", "massban", "security level"} <= senior)
        self.assertEqual(section_keys(EVERYTHING), {k for k, *_ in SECTIONS})
        self.assertEqual(hidden_commands(EVERYTHING), set())
        self.assertIn("add-money", hidden_commands(MEMBER))

    async def test_staff_get_a_private_reply_and_members_a_public_one(self):
        member = await self.run_command("help", self.alice)
        self.assertFalse(member.log[0][1]["ephemeral"])
        self.assertNotIn("Admin", member.text())
        admin = await self.run_command("help", self.admin)
        self.assertTrue(admin.log[0][1]["ephemeral"])
        self.assertIn("Admin", admin.text())

    async def test_section_autocomplete_offers_only_visible_sections(self):
        cog = self.cog("HelpCog")
        member = [c.value for c in await cog._sections(FakeInteraction(self.bot, self.alice), "")]
        self.assertIn("economy", member)
        self.assertIn("full", member)
        self.assertFalse(set(member) & {"banker", "admin", "security"})
        admin = [c.value for c in await cog._sections(FakeInteraction(self.bot, self.admin), "")]
        self.assertTrue({"banker", "admin"} <= set(admin))

    async def test_clicking_someone_elses_menu_shows_your_own_commands(self):
        it = FakeInteraction(self.bot, self.alice)
        it.data = {"values": ["admin"]}                       # an admin's option, clicked by a member
        it.message = SimpleNamespace(interaction_metadata=SimpleNamespace(user=SimpleNamespace(id=self.admin.id)),
                                     flags=SimpleNamespace(ephemeral=False))
        await SectionSelect().callback(it)
        self.assertTrue(it.log[0][1]["ephemeral"])
        self.assertNotIn("add-money", it.text())

    async def test_ask_never_points_members_at_staff_commands(self):
        assistant = self.s.assistant
        hidden = frozenset(hidden_commands(MEMBER))
        member = assistant.local_answer("how do I add money to someone", hidden=hidden)
        self.assertNotIn("/add-money", member.commands)
        self.assertNotIn("add-money", member.text)
        admin = assistant.local_answer("how do I add money to someone")
        self.assertIn("/add-money", admin.commands)
        import json
        raw = json.dumps({"answer": "Use the commands.", "suggested_commands": ["/ban", "/banker account", "/balance"]})
        self.assertEqual(assistant._validate(raw, hidden).commands, ["/balance"])
        self.assertEqual(assistant._validate(raw).commands, ["/ban", "/banker account", "/balance"])


class PickerVisibilityTests(CogTestCase):
    def test_staff_commands_are_hidden_in_discords_picker_and_member_commands_are_not(self):
        from rudhabot.cogs.checks import PICKER_PERMISSIONS
        order = ["helper", "moderator", "senior", "banker", "admin"]
        entries = {}
        for c in get_commands_data():
            top = c["name"] if c["name"][:1].isupper() else c["name"].split()[0]
            entries.setdefault(top, []).append(REQUIRES.get(c["name"]))
        for command in self.bot.tree.get_commands():
            needs = entries.get(command.name, [None])
            granted = getattr(command, "default_permissions", None)
            if None in needs:                                     # something in it is for everyone
                self.assertIsNone(granted, f"/{command.name} would be hidden from members")
                continue
            # a group needs the permission of its lowest-ranked subcommand (Discord applies it to the whole group)
            lowest = min(needs, key=order.index)
            self.assertEqual(granted, PICKER_PERMISSIONS[lowest], f"/{command.name}")

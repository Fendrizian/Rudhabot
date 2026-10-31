"""/help: sectioned overview, single sections, the full list, and never-expiring navigation."""
from types import SimpleNamespace

import discord

from rudhabot.views.help import (SECTIONS, NavButton, SectionSelect, full_pages, get_commands_data, ordered_commands,
                                 render)
from tests.cog_case import CogTestCase
from tests.fakes import FakeInteraction


class HelpDataTests(CogTestCase):
    def test_every_command_is_in_exactly_one_section(self):
        names = [c["name"] for c in get_commands_data()]
        placed = [n for *_, section in SECTIONS for n in section]
        self.assertEqual(sorted(placed), sorted(names))

    def test_every_registered_slash_command_is_documented(self):
        documented = {c["name"].split()[0] for c in get_commands_data()}
        registered = {c.name for c in self.bot.tree.get_commands()}
        self.assertEqual(registered - documented, set())

    def test_full_list_shows_every_command_once_alphabetically(self):
        seen = []
        for page in range(full_pages()):
            embed, _ = render("full", page)
            seen += [f.name for f in embed.fields]
        self.assertEqual(len(seen), len(get_commands_data()))
        self.assertEqual(len(set(seen)), len(seen))
        self.assertEqual([c["name"] for c in ordered_commands()],
                         sorted((c["name"] for c in get_commands_data()), key=str.casefold))

    def test_every_view_lists_commands_alphabetically(self):
        # house rule: alphabetical in every section, in the full list and in the overview samples
        for key, *_ in SECTIONS:
            names = [f.name.lstrip("/").split(" <")[0].split(" [")[0] for f in render(key)[0].fields]
            self.assertEqual(names, sorted(names, key=str.casefold), key)
        seen = []
        for page in range(full_pages()):
            seen += [f.name.lstrip("/") for f in render("full", page)[0].fields]
        self.assertEqual(seen, sorted(seen, key=str.casefold))
        for field in render("home")[0].fields:
            samples = __import__("re").findall(r"`/([^`]+)`", field.value)
            self.assertTrue(all(a.casefold() <= b.casefold() for a, b in zip(samples, samples[1:])), field.name)

    def test_every_page_embeds_the_dri_emblem_and_keeps_the_bot_icon(self):
        from rudhabot.ui.theme import BOTICON
        from rudhabot.views.help import HELP_IMAGE
        self.assertTrue(HELP_IMAGE.endswith("dri.png"))
        for target in ["home", "full"] + [k for k, *_ in SECTIONS]:
            embed = render(target)[0]
            self.assertEqual(embed.image.url, "attachment://dri.png", target)
        self.assertTrue(render("home")[0].title.startswith(BOTICON))
        self.assertIn("Copyright 2026.", render("home")[0].description)


class HelpCommandTests(CogTestCase):
    async def test_plain_help_shows_the_section_overview_with_navigation(self):
        it = await self.run_command("help", self.alice)
        embed, view = it.log[0][1]["embed"], it.log[0][1]["view"]
        self.assertEqual(it.log[0][1]["file"].filename, "dri.png")        # the emblem is uploaded with the message
        self.assertEqual(len(embed.fields), len(SECTIONS))
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
        full = await self.run_command("help", self.alice, section=discord.app_commands.Choice(name="Full", value="full"))
        self.assertIn(f"Page 1/{full_pages()}", full.last_embed.footer.text)

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
        other = click(self.bob, self.alice.id)
        await SectionSelect().callback(other)
        self.assertEqual(other.log[0][0], "send")
        self.assertTrue(other.log[0][1]["ephemeral"])
        self.assertEqual(other.log[0][1]["file"].filename, "dri.png")    # private copies carry their own upload
        self.assertNotIn("file", owner.log[0][1])                          # edits reuse the original attachment

    async def test_navigation_works_from_custom_ids_alone(self):
        # e.g. after a restart: discord.py rebuilds the item from its id
        button = NavButton("full", 2)
        rebuilt = await NavButton.from_custom_id(None, button.item, NavButton.__discord_ui_compiled_template__.match(button.item.custom_id))
        it = FakeInteraction(self.bot, self.alice)
        await rebuilt.callback(it)
        self.assertIn(f"Page 3/{full_pages()}", it.last_embed.footer.text)
        home = FakeInteraction(self.bot, self.alice)
        await NavButton("home", 0).callback(home)
        self.assertEqual(len(home.last_embed.fields), len(SECTIONS))

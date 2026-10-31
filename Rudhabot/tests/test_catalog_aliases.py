"""Item aliases: unambiguous, forgiving to type, shown in /inventory, and /look describes an item."""
import unittest

from rudhabot import catalog_data
from rudhabot.domain.catalog import ItemCatalog, normalize
from rudhabot.ui.theme import Colors
from rudhabot.views.inventory import create_inventory_embed
from tests.cog_case import CogTestCase, FakeUser


class AliasTableTests(unittest.TestCase):
    def setUp(self):
        self.catalog = ItemCatalog()

    def test_every_alias_belongs_to_exactly_one_real_item(self):
        items = set(self.catalog.all_items())
        self.assertLessEqual(set(catalog_data.item_aliases), items)
        owners = {}
        for item, aliases in catalog_data.item_aliases.items():
            for alias in aliases:
                key = normalize(alias)
                self.assertNotIn(key, {normalize(i) for i in items if i != item}, f"{alias!r} is another item's name")
                self.assertEqual(owners.setdefault(key, item), item, f"{alias!r} is used by two items")

    def test_every_item_and_alias_resolves_to_itself(self):
        for item in self.catalog.all_items():
            self.assertEqual(self.catalog.resolve(item), item)
            for alias in self.catalog.aliases(item):
                self.assertEqual(self.catalog.resolve(alias), item, alias)

    def test_matching_ignores_case_accents_hyphens_and_plurals(self):
        r = self.catalog.resolve
        self.assertEqual(r("Cliche Romance Novel"), "cliché romance novel")     # the old table missed the accented name
        self.assertEqual(r("  T SHIRT "), "archie t-shirt")
        self.assertEqual(r("sing a song phone"), "sing-a-song phone")
        self.assertEqual(r("mugs"), "traditional niraya mug")
        self.assertEqual(r("sneaker"), "sketchers sneakers")
        self.assertEqual(r("shoes"), "mikey shoes")                               # no longer shared with the sneakers
        self.assertIsNone(r("nonexistent thing"))
        self.assertIsNone(r(""))

    def test_inventory_shows_aliases_as_grey_subtext(self):
        embed = create_inventory_embed(self.catalog, [("baseball cap", 1), ("biscuit", 2)], FakeUser(1, "a"), 0, 1)
        cap, biscuit = embed.fields
        self.assertIn("-# Aliases: cap, baseball", cap.value)
        self.assertNotIn("Aliases", biscuit.value)                                # nothing to show for one-word items

    def test_autocomplete_search_puts_exact_and_prefix_matches_first(self):
        self.assertEqual(self.catalog.search("cap")[0], "baseball cap")
        self.assertIn("fortune cookie", self.catalog.search("cook"))


class LookCommandTests(CogTestCase):
    async def test_look_shows_description_price_resale_and_aliases(self):
        self.fund(self.alice, 0, inventory={"baseball cap": 3})
        it = await self.run_command("look", self.alice, item="CAP")
        e = it.last_embed
        self.assertIn("Baseball Cap", e.title)
        self.assertEqual(e.color.value, Colors.SEARCH_ITEM)
        self.assertEqual(e.description, catalog_data.item_descriptions["baseball cap"])
        fields = {f.name: f.value for f in e.fields}
        self.assertTrue(fields["Price"].endswith("50") and fields["Sells for"].endswith("25"))
        self.assertEqual(fields["You own"], "3")
        self.assertIn("cap", fields["Aliases"])

    async def test_look_at_a_monthly_special_names_its_month(self):
        it = await self.run_command("look", self.alice, item="eggnog")
        fields = {f.name: f.value for f in it.last_embed.fields}
        self.assertEqual(fields["Type"], "Café monthly special")
        self.assertEqual(fields["On the menu"], "December")
        self.assertEqual(fields["/use"], "Drink it.")

    async def test_lore_replaces_the_description_once_written(self):
        self.s.catalog._lore["sandals"] = "Worn by every ferryman of the outer islands."
        it = await self.run_command("look", self.alice, item="sandals")
        self.assertTrue(it.last_embed.description.startswith("Worn by every ferryman"))

    async def test_unknown_item(self):
        it = await self.run_command("look", self.alice, item="dragon egg")
        self.assertIn("not found", it.last_embed.description)


if __name__ == "__main__":
    unittest.main()

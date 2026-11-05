"""Role incomes: /set-income (Admin), /income (everyone), /collect (your roles, each on its own cooldown)."""
from types import SimpleNamespace

from rudhabot.services.incomes import IncomeRefused
from tests.cog_case import CogTestCase
from rudhabot.ui.theme import CURRENCY
from tests.fakes import FakeGuild, FakeUser

KNIGHT, NOBLE, OTHER = 7001, 7002, 7003


def role(rid, default=False, managed=False):
    return SimpleNamespace(id=rid, mention=f"<@&{rid}>", name=f"role{rid}", managed=managed, is_default=lambda: default)


class RoleIncomeTests(CogTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.incomes = self.s.incomes
        self.guild = FakeGuild(1)
        self.knight = FakeUser(1101, "knight", roles=[SimpleNamespace(id=KNIGHT), SimpleNamespace(id=NOBLE)])

    async def set_income(self, rid, amount, hours=None, actor=None):
        kwargs = {"role": role(rid), "amount": amount}
        if hours is not None:
            kwargs["hours"] = hours
        return await self.run_command("set-income", actor or self.admin, self.guild, **kwargs)

    async def test_admins_set_change_and_remove_incomes_and_it_is_audited(self):
        it = await self.set_income(KNIGHT, 500)
        self.assertIn(f"no income → **{CURRENCY}500 every day**", it.last_embed.description)
        self.assertTrue(it.log[0][1]["ephemeral"])
        post = self.s.messenger.post.await_args
        self.assertEqual(post.args[0], self.s.profile.channel("audit"))
        self.assertIn("Role income changed", post.kwargs["embed"].title)
        await self.set_income(KNIGHT, 800)                                        # hours kept when left out
        self.assertEqual((self.incomes.get(KNIGHT).amount, self.incomes.get(KNIGHT).hours), (800, 24))
        await self.set_income(KNIGHT, 800, hours=168)
        self.assertEqual(self.incomes.get(KNIGHT).hours, 168)
        await self.set_income(KNIGHT, 0)
        self.assertIsNone(self.incomes.get(KNIGHT))
        self.assertEqual([h["new"] for h in self.incomes.history()], [[500, 24], [800, 24], [800, 168], None])

    async def test_only_admins_and_only_real_roles(self):
        it = await self.set_income(KNIGHT, 500, actor=self.alice)
        self.assertIsNone(self.incomes.get(KNIGHT))
        self.assertNotIn("✅", it.text())
        it = await self.run_command("set-income", self.admin, self.guild, role=role(1, default=True), amount=10)
        self.assertIn("can't have an income", it.last_embed.description)
        it = await self.run_command("set-income", self.admin, self.guild, role=role(2, managed=True), amount=10)
        self.assertIn("can't have an income", it.last_embed.description)
        with self.assertRaises(IncomeRefused):
            self.incomes.set(KNIGHT, 0, None, by=1)                               # nothing to remove
        with self.assertRaises(IncomeRefused):
            self.incomes.set(KNIGHT, 10, 0, by=1)
        for rid in range(25):
            self.incomes.set(9000 + rid, 10, None, by=1)
        with self.assertRaises(IncomeRefused):
            self.incomes.set(KNIGHT, 10, None, by=1)                              # at most 25 income roles

    async def test_collect_pays_each_role_once_per_cooldown(self):
        await self.set_income(KNIGHT, 500)
        await self.set_income(NOBLE, 200, hours=6)
        await self.set_income(OTHER, 999)                                          # not one of the knight's roles
        it = await self.run_command("collect", self.knight, self.guild)
        self.assertIn(f"You collected {CURRENCY}700", it.last_embed.title)
        self.assertEqual(self.account(self.knight)["wallet"], 700)
        it = await self.run_command("collect", self.knight, self.guild)
        self.assertIn("Nothing to collect yet", it.last_embed.title)
        self.assertTrue(it.log[0][1]["ephemeral"])
        self.assertIn("ready in", it.last_embed.description)
        self.clock.advance(hours=6)
        it = await self.run_command("collect", self.knight, self.guild)
        self.assertIn(f"{CURRENCY}200", it.last_embed.title)                       # the 6-hour role only
        self.assertEqual(self.account(self.knight)["wallet"], 900)
        self.clock.advance(hours=60)                                               # missed periods don't pile up
        await self.run_command("collect", self.knight, self.guild)
        self.assertEqual(self.account(self.knight)["wallet"], 1600)
        entries = self.s.audit.recent(10, self.knight.id)
        self.assertTrue(entries and all(e["account"] == "wallet" for e in entries))

    async def test_members_without_income_roles_are_pointed_to_the_list(self):
        await self.set_income(KNIGHT, 500)
        it = await self.run_command("collect", self.alice, self.guild)
        self.assertIn("/income", it.last_embed.description)
        self.assertEqual(self.account(self.alice)["wallet"], 0)

    async def test_income_lists_every_role_and_marks_yours(self):
        it = await self.run_command("income", self.alice, self.guild)
        self.assertIn("No role has an income yet", it.last_embed.description)
        await self.set_income(KNIGHT, 500)
        await self.set_income(OTHER, 999, hours=168)
        it = await self.run_command("income", self.knight, self.guild)
        text = it.last_embed.description
        self.assertLess(text.index(f"<@&{OTHER}>"), text.index(f"<@&{KNIGHT}>"))   # biggest first
        self.assertIn("every week", text)
        self.assertIn("✅ ready", text)
        await self.run_command("collect", self.knight, self.guild)
        it = await self.run_command("income", self.knight, self.guild)
        self.assertIn("yours in", it.last_embed.description)

    async def test_incomes_of_deleted_roles_are_dropped(self):
        await self.set_income(KNIGHT, 500)
        await self.set_income(OTHER, 100)
        self.guild.roles = [SimpleNamespace(id=KNIGHT)]
        await self.run_command("income", self.alice, self.guild)
        self.assertIsNone(self.incomes.get(OTHER))
        self.assertIsNotNone(self.incomes.get(KNIGHT))
        self.assertEqual(self.incomes.history()[-1].get("note"), "role deleted")

    async def test_collections_survive_a_restart(self):
        await self.set_income(KNIGHT, 500)
        await self.run_command("collect", self.knight, self.guild)
        from rudhabot.services.incomes import RoleIncomes
        s = self.s
        again = RoleIncomes(s.storage.value("role_incomes", s.settings.paths.role_incomes, dict),
                            s.storage.docs("income_collections", s.settings.paths.income_collections), s.users, s.clock)
        self.assertIsNotNone(again.ready_in(self.knight.id, again.get(KNIGHT)))

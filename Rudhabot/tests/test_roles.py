"""The temporary /role tools: Admin-only, below Rudhabot's role only, no integration roles or @everyone, no taking
roles from equal or higher ranks, Discord's move semantics, mod-log cases, anti-nuke accounting, the off switch."""
from dataclasses import replace
from types import SimpleNamespace

from rudhabot.security.actions import Pacer
from rudhabot.security.clearance import Refused
from rudhabot.security.fake import demo_guild
from rudhabot.security.model import MemberModel, RoleModel
from tests.cog_case import CogTestCase
from tests.fakes import FakeGuild, FakeUser

OWNER, MOD, ADMIN, ADMIN2, MEMBER = 10, 22, 24, 26, 4242
CITIZEN, PROBATION, HELPER, MODERATOR, SENIOR, ADMIN_ROLE, OTHER_BOT, RUDHABOT = 2, 7, 3, 4, 5, 6, 9, 8


async def no_sleep(_):
    return None


class RoleToolTests(CogTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake = demo_guild()
        self.fake.add_member(MemberModel(MEMBER, (CITIZEN,)))
        self.fake.add_member(MemberModel(ADMIN2, (CITIZEN, ADMIN_ROLE)))
        self.sec = self.s.security
        self.sec.pacer_factory = lambda: Pacer(rate=1000, burst=1000, sleep=no_sleep)
        self.sec.attach(1, self.fake)
        self.notes = []
        original = self.sec.notify

        async def notify(kind, guild_id, payload):
            self.notes.append((kind, payload))
            await original(kind, guild_id, payload)
        self.sec.notify = notify

    def roles_of(self, uid):
        return self.fake.members[uid].roles

    def position(self, role_id):
        return self.fake.model().role(role_id).position

    async def test_an_admin_can_give_and_take_roles_below_rudhabot(self):
        message, case = await self.sec.role_assign(1, ADMIN, MEMBER, SENIOR, True, "promotion")
        self.assertIn(SENIOR, self.roles_of(MEMBER))
        self.assertEqual((case["kind"], case["extra"]["role_name"]), ("role_add", "Senior Moderator"))
        self.assertIn("role_change", [k for k, _ in self.notes])             # dangerous permissions: staff alerted
        self.assertTrue(self.sec.runtime(1).engine.antinuke.actors[ADMIN].spends)   # counted against the admin
        message, case = await self.sec.role_assign(1, ADMIN, MEMBER, SENIOR, True, "again")
        self.assertIsNone(case)
        self.assertIn("already has", message)
        message, case = await self.sec.role_assign(1, ADMIN, MEMBER, SENIOR, False, "demotion")
        self.assertNotIn(SENIOR, self.roles_of(MEMBER))
        self.assertEqual(case["kind"], "role_remove")

    async def test_admins_can_lift_themselves_above_their_own_hierarchy(self):
        await self.sec.role_assign(1, ADMIN, ADMIN, SENIOR, True, "needed for setup")
        self.assertIn(SENIOR, self.roles_of(ADMIN))

    async def test_only_admins_and_only_manageable_roles(self):
        with self.assertRaises(Refused):
            await self.sec.role_assign(1, MOD, MEMBER, HELPER, True, "x")                 # needs Admin
        for role in (OTHER_BOT, RUDHABOT, 1):                                             # integration, ours, @everyone
            with self.assertRaises(Refused):
                await self.sec.role_assign(1, ADMIN, MEMBER, role, True, "x")
        model = self.fake.model()
        self.fake._model = replace(model, roles=model.roles + (RoleModel(50, "Founder", 11, 0),))
        with self.assertRaises(Refused) as ctx:
            await self.sec.role_assign(1, ADMIN, MEMBER, 50, True, "x")                   # above Rudhabot's role
        self.assertIn("server owner", str(ctx.exception))
        with self.assertRaises(Refused):
            await self.sec.role_assign(1, ADMIN, 99, HELPER, True, "x")                   # Rudhabot itself

    async def test_nobody_takes_roles_from_an_equal_or_higher_rank(self):
        with self.assertRaises(Refused):
            await self.sec.role_assign(1, ADMIN, ADMIN2, ADMIN_ROLE, False, "coup")
        await self.sec.role_assign(1, OWNER, ADMIN2, ADMIN_ROLE, False, "owner's call")
        self.assertNotIn(ADMIN_ROLE, self.roles_of(ADMIN2))

    async def test_moving_roles_follows_discord_semantics_and_stays_below_rudhabot(self):
        message, case = await self.sec.role_move(1, ADMIN, HELPER, above=SENIOR, reason="restructure")
        self.assertGreater(self.position(HELPER), self.position(SENIOR))
        self.assertEqual(case["extra"]["from"], 3)
        await self.sec.role_move(1, ADMIN, HELPER, below=MODERATOR)
        self.assertEqual(self.position(HELPER), self.position(MODERATOR) - 1)
        await self.sec.role_move(1, ADMIN, CITIZEN, position=6)
        self.assertEqual(self.position(CITIZEN), 6)
        with self.assertRaises(Refused):
            await self.sec.role_move(1, ADMIN, HELPER, position=10)                       # Rudhabot's own level
        with self.assertRaises(Refused):
            await self.sec.role_move(1, ADMIN, HELPER, position=2, above=SENIOR)          # one option only
        positions = sorted(r.position for r in self.fake.model().roles)
        self.assertEqual(len(positions), len(set(positions)))                             # no two roles collide

    async def test_failures_and_the_off_switch(self):
        self.fake.refuse.add("add_role")
        with self.assertRaises(Refused) as ctx:
            await self.sec.role_assign(1, ADMIN, MEMBER, HELPER, True, "x")
        self.assertIn("Discord refused", str(ctx.exception))
        self.sec.role_tools = False
        with self.assertRaises(Refused):
            await self.sec.role_assign(1, ADMIN, MEMBER, HELPER, True, "x")

    async def test_the_overview_diagnoses_rudhabots_own_access(self):
        info = self.sec.role_overview(1, ADMIN)
        self.assertTrue(info["manage_roles"])
        self.assertEqual(info["bot_position"], 10)
        rows = {r["id"]: r for r in info["roles"]}
        self.assertTrue(rows[SENIOR]["manageable"])
        self.assertFalse(rows[OTHER_BOT]["manageable"])
        self.assertEqual(info["roles"][0]["id"], RUDHABOT)                              # highest first

    async def test_the_commands(self):
        guild = FakeGuild(1)
        admin = FakeUser(ADMIN, "admin")
        group = self.bot.tree.get_command("role")
        add = next(c for c in group.commands if c.name == "add")
        from tests.fakes import FakeInteraction
        it = FakeInteraction(self.bot, admin, guild)
        await add.callback(add.binding, it, SimpleNamespace(id=MEMBER), SimpleNamespace(id=HELPER), "help out")
        self.assertIn("Role given", it.last_embed.title)
        self.assertEqual(it.ephemeral(), [True])
        listing = next(c for c in group.commands if c.name == "list")
        it = FakeInteraction(self.bot, FakeUser(MOD, "mod"), guild)
        await listing.callback(listing.binding, it)
        self.assertIn("Not allowed", it.last_embed.title)

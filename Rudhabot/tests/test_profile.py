"""One home server and one place for its set-up: the server profile (/setup) is read live everywhere, changes to
roles that grant authority are guarded and announced, and commands from other servers are refused."""
import dataclasses
from types import SimpleNamespace

from rudhabot.security.actions import Pacer
from rudhabot.security.clearance import Tier
from rudhabot.security.fake import demo_guild
from rudhabot.security.model import MemberModel
from tests.cog_case import CogTestCase
from tests.fakes import FakeGuild, FakeInteraction, FakeUser


async def no_sleep(_):
    return None


def role(rid, default=False, managed=False):
    return SimpleNamespace(id=rid, mention=f"<@&{rid}>", managed=managed, is_default=lambda: default)


def channel(cid):
    return SimpleNamespace(id=cid, mention=f"<#{cid}>")


class ProfileTests(CogTestCase):
    async def test_defaults_overrides_reset_and_history(self):
        profile = self.s.profile
        settings = self.s.settings
        self.assertEqual(profile.channel("lottery"), settings.channels.lottery)
        self.assertEqual(profile.role("bankrupt"), settings.loans.bankrupt_role_id)
        profile.set("lottery", 4444, by=1)
        self.assertEqual(profile.channels.lottery, 4444)
        self.assertEqual(profile.channels.audit, settings.channels.audit)          # the rest untouched
        from rudhabot.services.profile import ServerProfile
        again = ServerProfile(profile._store, settings)
        self.assertEqual(again.channel("lottery"), 4444)                          # kept in storage
        profile.set("lottery", None, by=1)
        self.assertEqual(profile.channel("lottery"), settings.channels.lottery)
        self.assertEqual([h["key"] for h in profile.history()], ["lottery", "lottery"])

    async def test_everything_reads_the_profile_live(self):
        s = self.s
        s.profile.set("lottery", 4444, by=1)
        await s.announcer.jackpot(self.alice.id, s.lottery.specs[0])
        self.assertEqual(s.messenger.post.await_args.args[0], 4444)
        s.profile.set("admin", 7777, by=1)
        member = FakeUser(5, "x", roles=[SimpleNamespace(id=7777)])
        self.assertTrue(s.admin.is_admin(member))
        self.assertEqual(s.security.admin_role_id, 7777)
        s.profile.set("banker", 8888, by=1)
        self.assertTrue(s.admin.is_banker(FakeUser(6, "y", roles=[SimpleNamespace(id=8888)])))

    async def test_security_admin_tier_follows_the_admin_role(self):
        fake = demo_guild()
        self.s.security.pacer_factory = lambda: Pacer(rate=1000, burst=1000, sleep=no_sleep)
        self.s.security.attach(1, fake)
        fake.add_member(MemberModel(5005, (2, 77)))
        self.assertEqual(self.s.security.tier_of(1, 5005), Tier.MEMBER)
        self.s.profile.set("admin", 77, by=1)
        self.assertEqual(self.s.security.tier_of(1, 5005), Tier.ADMIN)


class SetupCommandTests(CogTestCase):
    async def test_admins_change_channels_and_roles_in_one_place(self):
        it = await self.run_command("setup", self.alice, FakeGuild(1))
        self.assertNotIn("Server setup", it.text())                              # members can't
        it = await self.run_command("setup", self.admin, FakeGuild(1), lottery_channel=channel(4444),
                                    banker_role=role(8888))
        self.assertIn("Server setup", it.last_embed.title)
        self.assertIn("<#4444>", it.text())
        self.assertEqual(self.s.profile.channel("lottery"), 4444)
        self.assertEqual(self.s.profile.role("banker"), 8888)
        it = await self.run_command("setup", self.admin, FakeGuild(1), reset="lottery")
        self.assertEqual(self.s.profile.channel("lottery"), self.s.settings.channels.lottery)

    async def test_the_admin_role_is_guarded(self):
        admin_role = self.s.profile.role("admin")
        role_admin = FakeUser(4321, "role-admin", roles=[SimpleNamespace(id=admin_role)])  # the role, not the permission
        it = await self.run_command("setup", role_admin, FakeGuild(1), admin_role=role(123))
        self.assertIn("Administrator permission", it.text())
        self.assertEqual(self.s.profile.role("admin"), admin_role)
        it = await self.run_command("setup", self.admin, FakeGuild(1), admin_role=role(1, default=True))
        self.assertIn("can't be used", it.text())
        self.assertEqual(self.s.profile.role("admin"), admin_role)
        notices = []

        async def notify(kind, gid, payload):
            notices.append((kind, payload))
        self.s.security.notify = notify
        await self.run_command("setup", self.admin, FakeGuild(1), admin_role=role(555))
        self.assertEqual(self.s.profile.role("admin"), 555)
        await self._sleep(0)
        await self._sleep(0)
        self.assertTrue(any(k == "settings" and p["weakening"] for k, p in notices))   # staff are told


class HomeServerTests(CogTestCase):
    async def test_other_servers_are_refused_when_a_home_is_set(self):
        self.bot.services = dataclasses.replace(self.s, settings=dataclasses.replace(self.s.settings, home_guild_id=1))
        other = FakeInteraction(self.bot, self.alice, FakeGuild(2))
        self.assertFalse(await self.bot.tree.interaction_check(other))
        self.assertIn("one server", other.last_embed.description)
        self.assertTrue(await self.bot.tree.interaction_check(FakeInteraction(self.bot, self.alice, FakeGuild(1))))
        self.assertTrue(await self.bot.tree.interaction_check(FakeInteraction(self.bot, self.alice, None)))

    async def test_no_home_set_refuses_nothing_and_finds_the_server_by_its_channels(self):
        self.assertTrue(await self.bot.tree.interaction_check(FakeInteraction(self.bot, self.alice, FakeGuild(2))))
        audit = self.s.profile.channel("audit")
        found = SimpleNamespace(guild=SimpleNamespace(id=31337))
        self.bot.get_channel = lambda cid: found if cid == audit else None
        self.assertEqual(self.bot._find_home(), 31337)

"""Member standing and how it ties the systems together: daily activity counts (never content), the score and band,
trusted members spared automatic restrictions, economy holds for contained or raid accounts, cross-references."""
import time
from datetime import timedelta, timezone

from rudhabot.security.actions import Pacer
from rudhabot.security.clearance import Tier
from rudhabot.security.events import Message
from rudhabot.security.fake import demo_guild
from rudhabot.security.levels import Level
from rudhabot.security.model import MemberModel
from rudhabot.security.responses import PlannedAction
from tests.cog_case import CogTestCase
from tests.fakes import FakeGuild, FakeUser

ADMIN = 24
VETERAN, NEWBIE = 5001, 5002


async def no_sleep(_):
    return None


def day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


class _StandingCase(CogTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake = demo_guild()
        self.sec = self.s.security
        self.sec.pacer_factory = lambda: Pacer(rate=1000, burst=1000, sleep=no_sleep)
        self.sec.attach(1, self.fake)
        self.standing = self.s.standing
        now = self.now()
        self.fake.add_member(MemberModel(VETERAN, (2,), joined_at=now - 300 * 86400))
        self.fake.add_member(MemberModel(NEWBIE, (2,), joined_at=now - 3600))

    def now(self):
        return self.clock.now(timezone.utc).timestamp()

    def active(self, user_id, days):
        now = self.now()
        self.sec.store.add_activity(1, {(user_id, day(now - d * 86400)): 3 for d in range(days)})


class StandingScoreTests(_StandingCase):
    async def test_bands_follow_tenure_activity_and_record(self):
        self.assertEqual(self.standing.of(1, NEWBIE).band, "new")
        self.active(VETERAN, 50)
        vet = self.standing.of(1, VETERAN)
        self.assertEqual(vet.band, "trusted")
        self.assertIn("clean record", vet.factors)
        self.sec.store.add_case(1, "ban", VETERAN, ADMIN, "old ban")
        self.assertLess(self.standing.of(1, VETERAN).score, vet.score)
        self.assertNotIn("clean record", self.standing.of(1, VETERAN).factors)

    async def test_trusted_takes_time_in_the_server(self):
        self.fake.add_member(MemberModel(5003, (2,), joined_at=self.now() - 10 * 86400))
        self.active(5003, 45)                                    # implausibly busy, but only 10 days in
        self.assertEqual(self.standing.of(1, 5003).band, "established")

    async def test_restriction_and_false_alarms(self):
        self.active(VETERAN, 50)
        self.sec.store.save_incident(1, {"id": "1234567", "opened": self.now(), "last": self.now(), "status": "closed",
                                         "family": "spam", "severity": 0.6, "users": [VETERAN]})
        with_incident = self.standing.of(1, VETERAN)
        self.assertEqual(len(with_incident.incidents), 1)
        self.sec.store.set_verdict(1, "1234567", "false", ADMIN, "spam", [], False)
        self.assertGreater(self.standing.of(1, VETERAN).score, with_incident.score)   # a false alarm doesn't count
        self.sec.store.set_probation(1, VETERAN, 7, self.now() + 3600, "test", None)
        self.assertEqual(self.standing.of(1, VETERAN).band, "restricted")

    async def test_messages_are_counted_per_day_without_content(self):
        for n in range(3):
            await self.sec.handle(1, Message(1, 101, 100 + n, NEWBIE, self.now() + n, content="hello there"))
        await self.sec.handle(1, Message(1, 101, 200, 98, self.now(), content="bot", author_bot=True))
        rt = self.sec.runtime(1)
        self.sec._flush_activity(rt)
        rows = self.sec.store.db.query("SELECT * FROM member_activity")
        self.assertEqual(rows, [(1, NEWBIE, day(self.now()), 3)])             # no text anywhere, no bots
        self.assertEqual(self.standing.of(1, NEWBIE).active_days, 1)


class TrustedProtectionTests(_StandingCase):
    async def test_automatic_timeout_of_a_trusted_member_goes_to_staff(self):
        self.active(VETERAN, 50)
        cfg = self.sec.config(1).with_value("mode", "live").with_value("alert_channel", 201)
        self.sec.save_config(1, cfg, ADMIN, "live")
        rt = self.sec.runtime(1)
        plan = PlannedAction("timeout", "auto", Tier.HELPER, "repeat spam", targets=[VETERAN, NEWBIE],
                             params={"seconds": 600})
        await self.sec._dispatch(rt, plan)
        self.assertIn(NEWBIE, self.fake.timeouts)                 # the new account is timed out
        self.assertNotIn(VETERAN, self.fake.timeouts)             # the trusted member is not ...
        pending = self.sec.store.pending_approvals(1)
        self.assertEqual(len(pending), 1)                         # ... staff get a proposal instead
        action = self.sec.store.action(pending[0]["action_id"])
        self.assertEqual(action["targets"], [VETERAN])
        self.assertEqual(self.sec.health["spared_trusted"], 1)

    async def test_probation_skips_trusted_members(self):
        self.active(VETERAN, 50)
        cfg = self.sec.config(1).with_value("mode", "live").with_value("probation_role", 7)
        self.sec.save_config(1, cfg, ADMIN, "live")
        rt = self.sec.runtime(1)
        await self.sec._dispatch(rt, PlannedAction("probation", "auto", Tier.HELPER, "cohort", targets=[VETERAN],
                                                   params={"minutes": 60}))
        self.assertNotIn(7, self.fake.members[VETERAN].roles)
        self.assertFalse(self.sec.store.on_probation(1, VETERAN))


class EconomyHoldTests(_StandingCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.guild = FakeGuild(1)
        self.fund(self.alice, 1000)
        self.fund(self.bob, 1000)

    async def test_probation_and_quarantine_freeze_transfers_both_ways(self):
        self.sec.store.set_probation(1, self.bob.id, 7, self.now() + 3600, "test", None)
        it = await self.run_command("send", self.alice, self.guild, target=self.bob, amount="100")
        self.assertIn("can't move money", it.last_embed.description)
        self.assertNotIn("probation", it.last_embed.description)
        self.assertEqual(self.account(self.bob)["wallet"], 1000)
        it = await self.run_command("send", self.bob, self.guild, target=self.alice, amount="100")
        self.assertIn("Your account", it.last_embed.description)
        it = await self.run_command("rob", self.alice, self.guild, target=self.bob)
        self.assertIn("can't move money", it.last_embed.description)
        self.sec.runtime(1).quarantined.add(self.alice.id)
        it = await self.run_command("loan", self.alice, self.guild, amount=500, days=5)
        self.assertIn("quarantined", it.last_embed.description)
        self.sec.store.end_probation(1, self.bob.id)
        self.sec.runtime(1).quarantined.discard(self.alice.id)
        it = await self.run_command("send", self.alice, self.guild, target=self.bob, amount="100")
        self.assertEqual(self.account(self.bob)["wallet"], 1100)

    async def test_new_accounts_cant_transfer_during_a_raid(self):
        self.bob.joined_at = self.clock.now(timezone.utc) - timedelta(hours=2)
        rt = self.sec.runtime(1)
        await self.run_command("send", self.alice, self.guild, target=self.bob, amount="10")
        self.assertEqual(self.account(self.bob)["wallet"], 1010)                  # level 1: fine
        rt.engine.set_level(Level(3), self.now(), ADMIN, "raid", 3600)
        it = await self.run_command("send", self.alice, self.guild, target=self.bob, amount="10")
        self.assertIn("can't move money", it.last_embed.description)
        self.assertNotIn("raid", it.last_embed.description)                     # not told why someone else is held
        self.assertEqual(self.account(self.bob)["wallet"], 1010)
        it = await self.run_command("send", self.bob, self.guild, target=self.alice, amount="10")
        self.assertIn("raid alert", it.last_embed.description)                  # the member themselves is told
        self.bob.joined_at = self.clock.now(timezone.utc) - timedelta(days=3)
        await self.run_command("send", self.alice, self.guild, target=self.bob, amount="10")
        self.assertEqual(self.account(self.bob)["wallet"], 1020)                  # older members aren't held


class CrossReferenceTests(_StandingCase):
    async def test_alerts_flag_possible_compromise_and_signals_show_standing(self):
        self.active(VETERAN, 50)
        lines = self.standing.cross_refs_for_incident(1, {"users": [VETERAN, NEWBIE]})
        self.assertTrue(any("possibly a compromised account" in line and str(VETERAN) in line for line in lines))
        self.assertFalse(any(str(NEWBIE) in line for line in lines))
        self.sec.store.save_incident(1, {"id": "7654321", "opened": self.now(), "last": self.now(), "status": "open",
                                         "family": "phishing", "severity": 0.7, "users": [NEWBIE]})
        refs = self.standing.cross_refs_for_signal(1, {"user": str(NEWBIE)})
        self.assertTrue(refs[0].startswith("Standing:"))
        self.assertTrue(any("7654321" in line for line in refs))

    async def test_member_file_shows_everything(self):
        self.active(VETERAN, 50)
        self.sec.store.add_case(1, "warn", VETERAN, ADMIN, "be nice")
        user = FakeUser(VETERAN, "veteran")
        it = await self.run_command("modlog", FakeUser(ADMIN, "admin"), FakeGuild(1), user=user)
        text = it.text()
        for part in ("Standing", "Why", "be nice", "Economy", "posted on 50"):
            self.assertIn(part, text)

    async def test_old_incidents_are_indexed_once(self):
        store = self.sec.store
        store.db.execute("INSERT INTO sec_incidents VALUES (1, '1111111', 0, 0, NULL, 'closed', 'spam', 0.5, ?)",
                         ('{"id": "1111111", "users": [777], "last": 0}',))
        store.db.execute("DELETE FROM meta WHERE key='sec_incident_users'")
        from rudhabot.persistence.security import SecurityStore
        again = SecurityStore(store.db, self.clock)
        self.assertEqual([i["id"] for i in again.incidents_for(1, 777)], ["1111111"])

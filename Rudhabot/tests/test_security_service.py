"""Server security, orchestration: shadow vs live mode, approvals (two-person, replay, expiry), lockdowns (overlap,
restart, expiry, manual report), quarantine and release, snapshots and restore, moderation commands and clearance,
probation and slowmode expiry, retention. Everything runs against the in-memory FakeGuild; nothing touches Discord."""
import os
import tempfile
import unittest
from datetime import datetime, timezone

from rudhabot.clock import FixedClock
from rudhabot.persistence.database import Database
from rudhabot.persistence.security import SecurityStore
from rudhabot.security import perms as P
from rudhabot.security.actions import Pacer
from rudhabot.security.clearance import Refused, Tier
from rudhabot.security.events import AuditEntry, MemberJoin, Message
from rudhabot.security.fake import demo_guild
from rudhabot.security.levels import Level
from rudhabot.security.model import MemberModel
from rudhabot.services.security import SecurityService

START = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
OWNER, HELPER, MOD, SENIOR, ADMIN, MOD2 = 10, 21, 22, 23, 24, 25


async def no_sleep(_):
    return None


class SecurityServiceCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clock = FixedClock(START)
        self.db = Database(os.path.join(self._tmp.name, "s.db"))
        self.addCleanup(self.db.close)
        self.fake = demo_guild()
        self.service = self.make_service()
        self.rt = self.service.runtime(1)

    def make_service(self):
        store = SecurityStore(self.db, self.clock)
        service = SecurityService(store, self.clock, port_factory=lambda gid: self.fake, bot_id=lambda: 99,
                                  pacer_factory=lambda: Pacer(rate=1000, burst=1000, sleep=no_sleep))
        self.sent = []

        async def notify(kind, guild_id, payload):
            self.sent.append((kind, payload))
        service.notify = notify
        return service

    @property
    def now(self):
        return self.clock.now(timezone.utc).timestamp()

    def configure(self, **values):
        cfg = self.service.config(1)
        for key, value in values.items():
            cfg = cfg.with_value(key.replace("__", "."), value)
        self.service.save_config(1, cfg, ADMIN, "test")

    def message(self, i, author, text, dt=0.0, channel=101, member_days=0.1, **kw):
        self.fake.post(channel, 50_000 + i)
        tier = int(self.service.tier_of(1, author))
        return Message(1, channel, 50_000 + i, author, self.now + dt, content=text, author_tier=tier,
                       joined_at=self.now - member_days * 86400, created_at=self.now - 30 * 86400, **kw)

    def kinds(self, name):
        return [p for k, p in self.sent if k == name]


class ModesAndAutomationTests(SecurityServiceCase):
    async def flood(self, author=777):
        self.fake.add_member(MemberModel(author, (2,)))
        for i in range(9):
            await self.service.handle(1, self.message(i, author, "cheap followers at my shop!!", dt=i * 0.4))

    async def test_shadow_mode_records_but_changes_nothing(self):
        await self.flood()
        self.assertEqual(len(self.fake.messages[101]), 9)                    # nothing deleted
        self.assertEqual(self.fake.timeouts, {})
        rows = self.db.query("SELECT status, shadow FROM sec_actions")
        self.assertTrue(rows and all(status == "shadow" and shadow for status, shadow in rows))

    async def test_live_mode_deletes_spam_times_out_and_logs_a_case(self):
        self.configure(mode="live")
        await self.flood()
        self.assertLess(len(self.fake.messages[101]), 4)
        self.assertEqual(self.fake.timeouts.get(777), 300)
        cases = self.service.store.cases_for(1, 777)
        self.assertEqual(cases[0]["kind"], "timeout")
        self.assertTrue(cases[0]["extra"]["automatic"])
        statuses = {s for (s,) in self.db.query("SELECT status FROM sec_actions")}
        self.assertIn("confirmed", statuses)

    async def test_automatic_actions_never_restrict_staff(self):
        self.configure(mode="live")
        for i in range(12):
            await self.service.handle(1, self.message(i, MOD, "same text again and again", dt=i * 0.3))
        self.assertNotIn(MOD, self.fake.timeouts)

    async def test_a_join_cohort_is_put_on_probation_and_released_when_it_expires(self):
        self.configure(mode="live", probation_role=7, probation_minutes=30)
        for j in range(12):
            uid = 3000 + j
            self.fake.add_member(MemberModel(uid, ()))
            await self.service.handle(1, MemberJoin(1, uid, self.now + j * 2, created_at=self.now - 7200,
                                                    has_avatar=False, name=f"giftbot{4000 + j}"))
        on_probation = [u for u, m in self.fake.members.items() if 7 in m.roles]
        self.assertGreaterEqual(len(on_probation), 8)
        self.assertGreaterEqual(self.service.runtime(1).engine.level, Level.HIGH)
        self.assertTrue(self.kinds("approval"))                              # pausing invites is proposed, not done
        self.assertEqual(self.fake.paused, {})
        self.clock.advance(minutes=31)
        await self.service.tick()
        self.assertFalse([u for u, m in self.fake.members.items() if 7 in m.roles])


class AntiNukeServiceTests(SecurityServiceCase):
    async def test_quarantine_strips_dangerous_roles_suspends_clearance_and_release_restores(self):
        self.configure(mode="live")
        self.assertEqual(self.service.tier_of(1, MOD), Tier.MODERATOR)
        for k in range(3):
            await self.service.handle(1, AuditEntry(1, "channel_delete", MOD, self.now + k, target_id=900 + k,
                                                    actor_tier=int(Tier.MODERATOR)))
        self.assertNotIn(4, self.fake.members[MOD].roles)                    # the Moderator role was removed
        self.assertEqual(self.service.tier_of(1, MOD), Tier.MEMBER)          # and their clearance suspended
        self.assertTrue(self.kinds("containment"))
        self.assertEqual(self.service.store.cases_for(1, MOD)[0]["kind"], "quarantine")
        with self.assertRaises(Refused):
            await self.service.release_quarantine(1, SENIOR, MOD)            # needs Admin
        await self.service.release_quarantine(1, ADMIN, MOD)
        self.assertIn(4, self.fake.members[MOD].roles)
        self.assertEqual(self.service.tier_of(1, MOD), Tier.MODERATOR)

    async def test_the_owner_is_never_quarantined(self):
        self.configure(mode="live")
        for k in range(6):
            await self.service.handle(1, AuditEntry(1, "channel_delete", OWNER, self.now + k, target_id=900 + k,
                                                    actor_tier=int(Tier.OWNER)))
        self.assertIn(2, self.fake.members[OWNER].roles)
        refused = self.db.query("SELECT status FROM sec_actions WHERE kind='quarantine'")
        self.assertTrue(all(s in ("failed", "refused") for (s,) in refused))


class ApprovalAndLockdownTests(SecurityServiceCase):
    async def test_two_person_lockdown_preview_apply_replay_and_lift(self):
        token, preview = await self.service.request_lockdown(1, SENIOR, "channels", [100], 30, "raid in public")
        self.assertTrue(any("general" in line for line in preview))
        with self.assertRaises(Refused):
            await self.service.approve(1, token, SENIOR)                     # not their own request
        with self.assertRaises(Refused):
            await self.service.approve(1, token, MOD)                        # not senior enough
        message, result = await self.service.approve(1, token, ADMIN)
        self.assertEqual(result.status, "confirmed")
        everyone = self.fake.model().channel(101).overwrite_for(1)
        self.assertTrue(everyone.deny & P.SEND_MESSAGES)
        with self.assertRaises(Refused):
            await self.service.approve(1, token, OWNER)                      # a replayed button does nothing
        lock = self.service.store.active_lockdowns(1)[0]
        report = await self.service.lift_lockdown(1, lock["id"], MOD, "all clear")
        self.assertEqual(report["manual"], [])
        self.assertIsNone(self.fake.model().channel(101).overwrite_for(1))  # back to no overwrite at all

    async def test_overlapping_lockdowns_are_refused(self):
        token, _ = await self.service.request_lockdown(1, SENIOR, "channels", [101], 30, "x")
        await self.service.approve(1, token, ADMIN)
        with self.assertRaises(Refused):
            await self.service.request_lockdown(1, SENIOR, "channels", [101], 30, "again")

    async def test_a_lockdown_survives_a_restart_and_lifts_itself_on_expiry(self):
        token, _ = await self.service.request_lockdown(1, SENIOR, "server", [], 15, "x")
        await self.service.approve(1, token, ADMIN)
        restarted = self.make_service()                                      # same database, fresh process
        restarted.runtime(1)
        self.clock.advance(minutes=16)
        await restarted.tick()
        self.assertEqual(restarted.store.active_lockdowns(1), [])
        self.assertIsNone(self.fake.model().channel(101).overwrite_for(1))

    async def test_a_moderator_change_during_lockdown_is_left_alone_and_reported(self):
        token, _ = await self.service.request_lockdown(1, SENIOR, "channels", [101, 102], 30, "x")
        await self.service.approve(1, token, ADMIN)
        await self.fake.set_overwrite(102, 1, "role", P.SEND_MESSAGES, 0, "mod reopened it")
        lock = self.service.store.active_lockdowns(1)[0]
        report = await self.service.lift_lockdown(1, lock["id"], MOD)
        self.assertEqual(len(report["manual"]), 1)
        self.assertEqual(self.fake.model().channel(102).overwrite_for(1).allow, P.SEND_MESSAGES)

    async def test_partial_failure_is_reported_not_hidden(self):
        self.fake.refuse.add("set_overwrite")
        token, _ = await self.service.request_lockdown(1, SENIOR, "channels", [101], 30, "x")
        _, result = await self.service.approve(1, token, ADMIN)
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.data["manual"])

    async def test_approvals_expire_and_can_be_declined(self):
        token, _ = await self.service.request_lockdown(1, SENIOR, "channels", [101], 30, "x")
        self.clock.advance(minutes=25)
        await self.service.tick()
        with self.assertRaises(Refused):
            await self.service.approve(1, token, ADMIN)
        token, _ = await self.service.request_lockdown(1, SENIOR, "channels", [101], 30, "y")
        self.assertIn("Declined", await self.service.deny(1, token, ADMIN))
        with self.assertRaises(Refused):
            await self.service.approve(1, token, ADMIN)

    async def test_system_proposals_need_two_different_staff(self):
        from rudhabot.security.responses import PlannedAction
        plan = PlannedAction("lockdown", "approve2", Tier.SENIOR, "level 4", targets=[101],
                             params={"scope": "channels", "minutes": 30})
        token = await self.service.request_approval(self.rt, plan, requested_by=None)
        message, result = await self.service.approve(1, token, SENIOR)
        self.assertIsNone(result)
        self.assertIn("one more", message)
        _, result = await self.service.approve(1, token, ADMIN)
        self.assertEqual(result.status, "confirmed")


class RestoreAndModerationTests(SecurityServiceCase):
    async def test_restore_recreates_deleted_channels_and_roles_from_a_snapshot(self):
        self.service.take_snapshot(1, "test")
        self.fake.delete_channel(102)
        self.fake.delete_role(3)
        token, preview = await self.service.request_restore(1, ADMIN, None)
        self.assertTrue(any("roleplay-battles" in line for line in preview))
        _, result = await self.service.approve(1, token, OWNER)
        self.assertIn(result.status, ("confirmed", "partial"))
        names = {c.name for c in self.fake.model().channels}
        self.assertIn("roleplay-battles", names)
        self.assertIn("Helper", {r.name for r in self.fake.model().roles})

    async def test_moderation_commands_respect_clearance_and_number_cases(self):
        self.fake.add_member(MemberModel(888, (2,)))
        result, cases = await self.service.moderate(1, HELPER, "timeout", [888], reason="spam", seconds=300)
        self.assertEqual(result.status, "confirmed")
        with self.assertRaises(Refused):
            await self.service.moderate(1, HELPER, "timeout", [888], reason="spam", seconds=3600)
        result, _ = await self.service.moderate(1, MOD, "timeout", [MOD2], reason="peer", seconds=60)
        self.assertEqual(result.status, "failed")                            # can't time out an equal
        with self.assertRaises(Refused):
            await self.service.moderate(1, MOD, "ban", [888], reason="x")
        result, cases2 = await self.service.moderate(1, SENIOR, "ban", [888, 889], reason="raid", delete_seconds=3600)
        self.assertEqual(set(result.done), {888, 889})
        self.assertEqual([c["case_no"] for c in cases + cases2], [1, 2, 3])
        warn = await self.service.warn(1, HELPER, 890, "be nice")
        self.assertEqual(warn["case_no"], 4)
        with self.assertRaises(Refused):
            await self.service.warn(1, HELPER, HELPER, "self")

    async def test_slowmode_reverts_on_its_timer(self):
        await self.service.slowmode(1, HELPER, 101, 30, revert_minutes=10)
        self.assertEqual(self.fake.model().channel(101).slowmode, 30)
        self.clock.advance(minutes=11)
        await self.service.tick()
        self.assertEqual(self.fake.model().channel(101).slowmode, 0)

    async def test_status_posture_reports_and_retention(self):
        self.configure(mode="live")
        await self.service.report(1, 5, self.message(1, 777, "dm me for free robux http://bit.ly/x"))
        status = self.service.status(1)
        self.assertEqual(status["mode"], "live")
        self.assertEqual(status["open_incidents"], 1)
        incident_id = status["incidents"][0]["id"]
        await self.service.tick()                                             # flushes the incident
        text, source = await self.service.incident_report(1, incident_id)
        self.assertEqual(source, "template")
        self.assertIn("Observed", text)
        self.assertEqual(len(self.service.store.snippets(1, incident_id)), 1)
        score, findings = self.service.posture(1)
        self.assertTrue(any(f.title == "No security alert channel" for f in findings))
        self.clock.advance(days=15)
        self.service.store.sweep(1, 14, 90)
        self.assertEqual(self.service.store.snippets(1, incident_id), [])


if __name__ == "__main__":
    unittest.main()


class LoopholeRegressionTests(SecurityServiceCase):
    """Round-3 review: ways a compromised staff account could disarm or route around the defences."""

    async def test_staff_tiers_refuse_everyone_integrations_and_admin_without_the_owner(self):
        with self.assertRaises(Refused):
            self.service.set_staff_tier(1, ADMIN, 1, 4)                     # @everyone
        with self.assertRaises(Refused):
            self.service.set_staff_tier(1, ADMIN, 9, 2)                     # another bot's managed role
        with self.assertRaises(Refused):
            self.service.set_staff_tier(1, ADMIN, 3, 4)                     # Admin tier: owner only
        with self.assertRaises(Refused):
            self.service.set_staff_tier(1, ADMIN, 2, 2)                     # Citizen: 1150 members
        self.service.set_staff_tier(1, ADMIN, 3, 2)
        self.service.set_staff_tier(1, OWNER, 2, 1)
        self.assertEqual(self.service.config(1)["tiers"], {"3": 2, "2": 1})

    async def test_anti_nuke_exemptions_need_an_admin_never_cover_yourself_and_are_capped(self):
        with self.assertRaises(Refused):
            await self.service.add_exemption(1, SENIOR, "user", str(MOD), "antinuke", 30)
        with self.assertRaises(Refused):
            await self.service.add_exemption(1, ADMIN, "user", str(ADMIN), "all", 7)
        with self.assertRaises(Refused):
            await self.service.add_exemption(1, ADMIN, "role", "6", "antinuke", 7)        # a role they hold
        text = await self.service.add_exemption(1, ADMIN, "user", str(MOD), "antinuke", 90)
        self.assertIn("at most 7 days", text)
        entry = self.service.store.allows(1)[0]
        self.assertLessEqual(entry["expires"] - self.now, 7 * 86400 + 1)
        self.assertTrue(any(k == "settings" and p["weakening"] for k, p in self.sent))   # announced
        await self.service.add_exemption(1, SENIOR, "domain", "partner.example", "messages", 30)

    async def test_weakening_settings_are_announced(self):
        import asyncio
        cfg = self.service.config(1).with_value("mode", "live")
        self.service.save_config(1, cfg, ADMIN, "go live")
        self.service.save_config(1, self.service.config(1).with_value("antinuke.budget_mod", 200), ADMIN, "x")
        self.service.save_config(1, self.service.config(1).with_value("mode", "shadow"), ADMIN, "y")
        await asyncio.sleep(0)
        notices = [p for k, p in self.sent if k == "settings"]
        self.assertEqual(len(notices), 3)
        self.assertEqual(notices[0]["weakening"], [])
        self.assertIn("antinuke.budget_mod loosened (8 -> 200)", notices[1]["weakening"])
        self.assertTrue(any("shadow" in w for w in notices[2]["weakening"]))

    async def test_massban_of_established_members_needs_a_second_approval(self):
        established = [7000 + i for i in range(15)]
        for uid in established:
            self.fake.add_member(MemberModel(uid, (2,), joined_at=self.now - 90 * 86400))
        newcomers = [8000 + i for i in range(5)]
        for uid in newcomers:
            self.fake.add_member(MemberModel(uid, (), joined_at=self.now - 600))
        out = await self.service.massban(1, SENIOR, established + newcomers, "raid", 3600)
        self.assertEqual(set(out["banned"]), set(newcomers))                # the raid accounts go at once
        self.assertEqual(out["awaiting"], 15)
        self.assertTrue(set(established).isdisjoint(self.fake.banned))
        with self.assertRaises(Refused):
            await self.service.approve(1, out["token"], SENIOR)             # not the requester
        await self.service.approve(1, out["token"], ADMIN)
        self.assertTrue(set(established) <= self.fake.banned)

    async def test_bans_through_rudhabot_count_against_the_moderator(self):
        self.configure(mode="live")
        refused = 0
        for uid in range(9100, 9114):
            self.fake.add_member(MemberModel(uid, (2,), joined_at=self.now - 90 * 86400))
            try:
                await self.service.moderate(1, SENIOR, "ban", [uid], reason="x")
            except Refused:
                refused += 1                                                 # quarantined: no clearance left
        self.assertNotIn(5, self.fake.members[SENIOR].roles)                # over budget: quarantined
        self.assertGreater(refused, 0)

    async def test_creating_an_admin_role_and_granting_it_trips_anti_nuke(self):
        from rudhabot.security import perms as P
        rt = self.service.runtime(1)
        await self.service.handle(1, AuditEntry(1, "role_create", MOD, self.now, target_id=555, actor_tier=2,
                                                perms_before=0, perms_after=P.ADMINISTRATOR))
        self.assertEqual(rt.engine.antinuke.role_perms[555], P.ADMINISTRATOR)
        await self.service.handle(1, AuditEntry(1, "member_role_update", MOD, self.now + 1, target_id=4242,
                                                actor_tier=2, roles_added=(555,)))
        self.assertTrue(any(i.family == "nuke" and MOD in i.users for i in rt.engine.incidents.values()))

    async def test_member_reports_are_rate_limited_and_deduplicated(self):
        first = await self.service.report(1, 5, self.message(1, 777, "free nitro here"))
        again = await self.service.report(1, 6, self.message(1, 777, "free nitro here"))
        self.assertEqual(first, again)                                       # the same message: one incident
        for i in range(2, 6):
            await self.service.report(1, 5, self.message(i, 777, f"spam {i}"))
        with self.assertRaises(Refused):
            await self.service.report(1, 5, self.message(9, 777, "one too many"))
        alerts = [k for k, _ in self.sent if k == "incident"]
        self.assertEqual(len(alerts), 1)

    async def test_expired_slowmode_leaves_a_moderators_later_change_alone(self):
        await self.service.slowmode(1, HELPER, 101, 30, revert_minutes=10)
        await self.fake.set_slowmode(101, 120, "a moderator set it by hand")
        self.clock.advance(minutes=11)
        await self.service.tick()
        self.assertEqual(self.fake.model().channel(101).slowmode, 120)

    async def test_bots_ranked_above_rudhabot_are_trusted_like_the_owner(self):
        from dataclasses import replace
        from rudhabot.security.model import RoleModel
        model = self.fake.model()
        self.fake._model = replace(model, roles=model.roles + (RoleModel(60, "Wick", 11, 8, managed=True, bot_id=97),))
        self.fake.add_member(MemberModel(97, (60,), bot=True))
        self.service._refresh(self.rt)
        self.configure(mode="live")
        for k in range(10):
            await self.service.handle(1, AuditEntry(1, "ban", 97, self.now + k, target_id=9000 + k, actor_bot=True))
        self.assertEqual(self.rt.engine.incidents, {})                        # Wick's raid clean-up is not a nuke
        score, findings = self.service.posture(1)
        self.assertFalse(any("Wick" in f.title for f in findings))

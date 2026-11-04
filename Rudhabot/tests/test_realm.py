"""The State of the Realm: daily economy snapshots from the audit ledger (SQL time index), the weekly report across
every system, its once-a-week posting, and /clairvoyance report. Also the SQLite ledger's indexed reads."""
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

from rudhabot.clock import FixedClock
from rudhabot.persistence.database import Database, SqliteLedger
from rudhabot.persistence.ledger import Ledger
from rudhabot.services.audit import AuditService, set_context
from rudhabot.ui.branding import is_branded
from tests.cog_case import NOW, CogTestCase
from tests.fakes import FakeGuild


class LedgerIndexTests(unittest.TestCase):
    def test_since_reads_a_time_window_on_both_backends(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "x.db"))
            ledgers = [SqliteLedger(db, "l"), Ledger(os.path.join(tmp, "l.jsonl"))]
            for ledger in ledgers:
                for i, ts in enumerate(("2026-01-01T10:00:00", "2026-01-02T09:00:00", "2026-01-02T23:59:59",
                                        "2026-01-03T00:00:00")):
                    ledger.append({"user": "1", "ts": ts, "delta": i})
                self.assertEqual([e["delta"] for e in ledger.since("2026-01-02", "2026-01-03")], [1, 2])
                self.assertEqual([e["delta"] for e in ledger.since("2026-01-02")], [1, 2, 3])
            db.close()

    def test_audit_recent_uses_the_sql_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "x.db"))
            audit = AuditService(SqliteLedger(db, "l"), FixedClock(datetime(2026, 1, 1)))
            for i in range(5):
                audit.record(1 + i % 2, "wallet", 10, 10 * (i + 1))
            self.assertEqual(len(audit.recent(3)), 3)
            self.assertEqual([e["user"] for e in audit.recent(10, 1)], ["1", "1", "1"])
            self.assertFalse(audit._indexed)                  # nothing was loaded into memory
            db.close()


class RealmTests(CogTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.realm = self.s.realm

    def earn(self, user, amount, label):
        set_context(label, user.id, "command")
        account = self.account(user)
        self.s.users.update(user.id, wallet=account["wallet"] + amount)

    async def test_daily_snapshot_counts_supply_and_where_money_came_from(self):
        self.earn(self.alice, 100, "/work")
        self.earn(self.alice, 50, "/daily")
        self.earn(self.bob, 30, "/work")
        self.earn(self.bob, -20, "/coinflip")
        snap = self.realm.take_snapshot(self.clock.now().date())
        self.assertEqual(snap["supply"], 160)
        self.assertEqual(snap["holders"], 2)
        self.assertEqual(snap["transactions"], 4)
        self.assertEqual(snap["active_accounts"], 2)
        self.assertEqual(snap["net_by_reason"], {"/work": 130, "/daily": 50, "/coinflip": -20})

    async def test_snapshots_are_taken_once_per_day(self):
        self.assertEqual(self.realm.snapshot_due(), NOW.date() - timedelta(days=1))
        self.realm.take_snapshot()
        self.assertIsNone(self.realm.snapshot_due())
        self.clock.advance(days=1)
        self.assertEqual(self.realm.snapshot_due(), NOW.date())

    async def test_weekly_report_reads_every_system(self):
        for d in range(7):
            self.earn(self.alice, 100, "/work")
            self.realm.take_snapshot(self.clock.now().date())
            self.clock.advance(days=1)
        from rudhabot.security.fake import demo_guild
        sec = self.s.security
        sec.attach(1, demo_guild())
        now = self.clock.now(timezone.utc).timestamp()
        sec.store.save_incident(1, {"id": "1234567", "opened": now - 3600, "last": now, "status": "closed",
                                    "family": "phishing", "severity": 0.6, "users": [1]})
        sec.store.add_activity(1, {(1001, self.clock.now(timezone.utc).strftime("%Y-%m-%d")): 12})
        report = self.realm.report(1)
        self.assertEqual(report["economy"]["supply"], 700)
        self.assertEqual(report["economy"]["supply_change"], 600)
        self.assertEqual(report["economy"]["sources"][0], ("/work", 700))
        self.assertEqual(report["security"]["incidents"], {"phishing": 1})
        self.assertEqual(report["community"], {"members": 1, "messages": 12,
                                               "busiest": self.clock.now(timezone.utc).strftime("%Y-%m-%d")})
        from rudhabot.ui.realm_embeds import report_embed
        embed = report_embed(report)
        self.assertTrue(is_branded(embed))
        names = " ".join(f.name for f in embed.fields)
        for part in ("Economy", "Security", "Community", "Clairvoyance"):
            self.assertIn(part, names)

    async def test_the_report_is_posted_once_on_sunday_evening(self):
        cog = self.cog("MaintenanceCog")
        self.assertEqual(NOW.date(), date(2026, 3, 15))          # a Sunday, 20:00 Rudhanese Time
        self.assertTrue(await cog.report_if_due())
        channel = self.s.messenger.post.await_args.args[0]
        self.assertEqual(channel, self.s.profile.channel("reports"))
        self.assertFalse(await cog.report_if_due())                # once a week
        self.clock.advance(days=1)
        self.assertFalse(await cog.report_if_due())                # Monday: not the report day

    async def test_clairvoyance_report_command(self):
        it = await self.run_command("clairvoyance", self.admin, FakeGuild(1), group="report")
        self.assertIn("State of the Realm", it.last_embed.title)
        self.assertTrue(is_branded(it.last_embed))
        it = await self.run_command("clairvoyance", self.alice, FakeGuild(1), group="report")
        self.assertNotIn("State of the Realm", it.text())

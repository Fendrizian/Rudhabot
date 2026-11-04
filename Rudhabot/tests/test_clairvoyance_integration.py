"""Clairvoyance across the bot: branding wherever a model contributed, one status view, and learning from this server
without new weights (security verdicts, review verdicts, intent outcomes)."""
import os
import tempfile
import unittest
from datetime import datetime, timezone

import discord

from rudhabot.clock import FixedClock
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.ledger import Ledger
from rudhabot.security.actions import Pacer
from rudhabot.security.clearance import Refused
from rudhabot.security.events import Message
from rudhabot.security.fake import demo_guild
from rudhabot.services.review import CALIBRATION_BOUNDS, ReviewService
from rudhabot.ui.branding import CLAIRVOYANCE_ICON_URL, brand, is_branded
from tests.cog_case import CogTestCase
from tests.fakes import FakeGuild, FakeInteraction, FakeUser

HELPER, MOD, ADMIN = 21, 22, 24


async def no_sleep(_):
    return None


class BrandingTests(unittest.TestCase):
    def test_brand_keeps_existing_footer_text_and_adds_the_logo(self):
        embed = discord.Embed(title="x")
        embed.set_footer(text="match 123")
        brand(embed, "coach")
        self.assertEqual(embed.footer.text, "Clairvoyance · coach · match 123")
        self.assertEqual(embed.footer.icon_url, CLAIRVOYANCE_ICON_URL)
        self.assertTrue(CLAIRVOYANCE_ICON_URL.startswith("https://cdn.discordapp.com/emojis/"))
        again = discord.Embed()
        again.set_footer(text="win% by Clairvoyance v3")
        self.assertEqual(brand(again).footer.text, "win% by Clairvoyance v3")

    def test_model_outputs_are_branded(self):
        from rudhabot.tcg.cards import CARDS
        from rudhabot.ui.security_embeds import incident_embed
        from rudhabot.ui.tcg_embeds import card_embed
        card = CARDS[0]
        self.assertTrue(is_branded(card_embed(card, similar=[(CARDS[1].id, 0.9)])))
        self.assertFalse(is_branded(card_embed(card)))
        inc = {"id": "1234567", "family": "phishing", "severity": 0.6, "signals": [{"source": "model", "reasons": []}],
               "users": [], "channels": []}
        self.assertTrue(is_branded(incident_embed(inc, 1, False)))
        inc["signals"] = [{"source": "rule", "reasons": []}]
        self.assertFalse(is_branded(incident_embed(inc, 1, False)))


class ReviewCalibrationTests(unittest.TestCase):
    def test_moderators_verdicts_move_the_anomaly_bar_within_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = FixedClock(datetime(2026, 5, 1, 12))
            store = JsonStore(os.path.join(tmp, "r.json"), dict)
            signals = [{"id": f"s{i}", "user": str(i), "hour": "2026-05-01T12", "opened": "2026-05-01T12:00:00",
                        "score": 0.7, "kind": "model", "reasons": [], "status": "open", "resolved_by": None,
                        "note": None} for i in range(12)]
            store.save({"signals": signals})
            svc = ReviewService(store, Ledger(os.path.join(tmp, "l.jsonl")), Ledger(os.path.join(tmp, "f.jsonl")), clock)
            base = svc.detector.threshold
            for i in range(4):
                svc.resolve(f"s{i}", 9, "dismissed")
            self.assertEqual(svc.detector.threshold, base)                    # fewer than 5 verdicts: unchanged
            for i in range(4, 12):
                svc.resolve(f"s{i}", 9, "dismissed")
            self.assertEqual(svc.detector.threshold, CALIBRATION_BOUNDS[1])   # all false alarms: the bar tops out
            again = ReviewService(store, Ledger(os.path.join(tmp, "l.jsonl")), Ledger(os.path.join(tmp, "f.jsonl")),
                                  clock)
            self.assertEqual(again.detector.threshold, CALIBRATION_BOUNDS[1])  # remembered across restarts


class LearningFromStaffTests(CogTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake = demo_guild()
        self.sec = self.s.security
        self.sec.pacer_factory = lambda: Pacer(rate=1000, burst=1000, sleep=no_sleep)
        self.sec.attach(1, self.fake)

    def now(self):
        return self.clock.now(timezone.utc).timestamp()

    async def incident(self, n):
        await self.sec.handle(1, Message(1, 101, 70_000 + n, 5000 + n, self.now(), content="x",
                                               joined_at=self.now() - 86400 * 30))
        rt = self.sec.runtime(1)
        sig = rt.engine.observe_model(Message(1, 101, 80_000 + n, 6000 + n, self.now() + n * 2000,
                                              content=f"claim at https://site{n}.example/x"), scam=0.99, spam=0.2)
        await self.sec._outcome(rt, sig)
        return (sig.opened or sig.updated)[0].id

    async def test_false_alarm_verdicts_raise_the_model_bar_and_real_ones_bring_it_back(self):
        ids = [await self.incident(n) for n in range(8)]
        for incident_id in ids:
            await self.sec.verdict(1, incident_id, HELPER, real=False)
        cal = self.sec.calibration(1)
        self.assertEqual(cal["model_verdicts"], 8)
        self.assertAlmostEqual(cal["message_bar"], 0.95)
        self.assertAlmostEqual(self.sec.runtime(1).engine.message_bar, 0.95)
        for incident_id in ids:
            await self.sec.verdict(1, incident_id, HELPER, real=True)            # staff change their minds
        self.assertAlmostEqual(self.sec.calibration(1)["message_bar"], 0.80)
        with self.assertRaises(Refused):
            await self.sec.verdict(1, ids[0], 4242, real=True)                   # members can't vote

    async def test_noisy_detectors_get_a_health_check_hint(self):
        ids = [await self.incident(n) for n in range(6)]
        for incident_id in ids:
            await self.sec.verdict(1, incident_id, HELPER, real=False)
        score, findings = self.sec.posture(1)
        self.assertTrue(any("mostly false alarms" in f.title for f in findings))

    async def test_status_shows_every_component_and_the_verdict_buttons_work(self):
        it = await self.run_command("clairvoyance", self.admin, FakeGuild(1), group="status")
        overview = it.embeds[0]
        names = [f.name for f in overview.fields]
        for part in ("Cloudfall", "Intent router", "Anomaly monitor", "Security", "Language model"):
            self.assertTrue(any(part in n for n in names), part)
        self.assertTrue(is_branded(overview))
        incident_id = await self.incident(0)
        press = FakeInteraction(self.bot, FakeUser(HELPER, "helper"), FakeGuild(1))
        await self.cog("SecurityCog").on_verdict_button(press, incident_id, False)
        self.assertIn("false alarm", press.last_embed.description)

    async def test_intent_outcomes_are_counted_without_text(self):
        from rudhabot.cogs.assistant import suggestion
        router = self.s.intents
        if not router.ready:
            self.skipTest("intent model not shipped")
        suggestion(router, 1, "show my inventory")
        self.assertGreaterEqual(router.outcomes.get("offered", 0) + router.outcomes.get("clarified", 0), 1)
        self.assertNotIn("show my inventory", str(router.outcomes))


if __name__ == "__main__":
    unittest.main()

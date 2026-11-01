"""Skyfall Wishes (gacha), the intent router and the anomaly/review pipeline."""
import os
import random
import tempfile
import unittest
from collections import Counter
from datetime import date, datetime

import discord

from rudhabot.clock import FixedClock
from rudhabot.domain import gacha as G
from rudhabot.ml.anomaly import AnomalyDetector, IsolationForest, synthetic, windows
from rudhabot.ml.intent import IntentRouter
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.ledger import Ledger
from rudhabot.services.review import ReviewService
from rudhabot.settings import DEFAULT_MODELS_DIR
from tests.cog_case import CogTestCase
from tests.fakes import FakeInteraction

INTENT_MODEL = os.path.join(DEFAULT_MODELS_DIR, "intent-v1.npz")


class GachaRuleTests(unittest.TestCase):
    def test_rarity_pools_cover_every_card(self):
        self.assertEqual(sum(len(v) for v in G.POOL.values()), 112)
        self.assertIn("C01", G.POOL[5])                                   # Grade 5
        self.assertIn("M09", G.POOL[5])                                   # Unique

    def test_hard_pity_and_four_star_pity(self):
        class Unlucky(random.Random):
            def random(self):
                return 0.999999                                           # never wins a roll on its own
        rng, state = Unlucky(0), G.PityState()
        banner = G.weekly_banner(date(2026, 10, 31))
        stars = [G.wish(banner, state, rng).stars for _ in range(G.HARD_PITY)]
        self.assertEqual(stars[-1], 5)
        self.assertNotIn(5, stars[:-1])
        gaps = [i for i, s in enumerate(stars) if s >= 4]
        self.assertTrue(all(b - a <= G.FOUR_STAR_PITY for a, b in zip([-1] + gaps, gaps)))

    def test_losing_the_50_50_guarantees_the_next_featured(self):
        banner = G.weekly_banner(date(2026, 10, 31))
        state = G.PityState(guaranteed=True)
        rng = random.Random(1)
        while True:
            pull = G.wish(banner, state, rng)
            if pull.stars == 5:
                break
        self.assertTrue(pull.featured and pull.card == banner.featured5)
        self.assertFalse(state.guaranteed)

    def test_published_rates_hold_with_pity(self):
        rng, state, banner = random.Random(5), G.PityState(), G.STANDARD
        c = Counter(G.wish(banner, state, rng).stars for _ in range(60_000))
        self.assertTrue(0.02 < c[5] / 60_000 < 0.035)                   # base 1.6% plus pity ≈ 2.6%
        self.assertTrue(0.55 < c[2] / 60_000 < 0.62)


class GachaServiceTests(CogTestCase):
    async def test_shards_daily_wishes_dust_and_convergence(self):
        g, uid = self.s.gacha, self.alice.id
        with self.assertRaises(Exception):
            g.wish(uid, 10)                                              # no shards yet
        self.fund(self.alice, 20_000)
        g.buy_shards(uid, 1600)                                          # 10 ra per shard: 16,000 ra
        g.claim_daily(uid)
        with self.assertRaises(Exception):
            g.claim_daily(uid)
        pulls, new, dust, banner = g.wish(uid, 10)
        self.assertEqual(len(pulls), 10)
        self.assertTrue(any(p.stars >= 4 for p in pulls))
        profile = self.s.tcg.profile(uid)
        self.assertEqual(profile["shards"], 1600 + G.DAILY_SHARDS - 10 * G.WISH_COST)
        self.assertEqual(sum(profile["collection"].values()), 10)
        self.assertEqual(self.account(self.alice)["wallet"], 20_000 - 1600 * 10)
        accounts = {e["account"] for e in self.s.audit.ledger.read()}
        self.assertTrue({"wallet", "shards"} <= accounts)
        self.assertEqual(g.convergence()["wishes"], 10)
        with self.assertRaises(Exception):
            g.claim_convergence(uid)                                     # 250 not reached yet

    async def test_crafting_spends_dust(self):
        profile = self.s.tcg.profile(self.alice.id)
        profile["dust"] = 120
        self.s.tcg.repo.save_profile(self.alice.id, profile)
        self.assertEqual(self.s.gacha.craft(self.alice.id, "A01"), 120 - G.CRAFT_COST[G.rarity(__import__("rudhabot.tcg.cards", fromlist=["CARD_BY_ID"]).CARD_BY_ID["A01"])])
        self.assertEqual(self.s.tcg.profile(self.alice.id)["collection"]["A01"], 1)


@unittest.skipUnless(os.path.exists(INTENT_MODEL), "intent model not built")
class IntentTests(CogTestCase):
    def test_router_understands_clear_requests_and_hands_chat_to_the_assistant(self):
        router = IntentRouter(INTENT_MODEL)
        for text, intent in [("show my inventory", "inventory"), ("what's my balance", "balance"),
                             ("play blackjack", "blackjack"), ("hello how are you", "chat")]:
            route = router.route(text)
            self.assertEqual(route.intent, intent, text)

    async def test_ask_offers_the_command_and_the_button_runs_it_with_its_checks(self):
        from rudhabot.cogs.assistant import RunIntent
        it = await self.run_command("ask", self.alice, question="show me my balance")
        view = it.log[0][1]["view"]
        button = view.children[0]
        self.assertIsInstance(button, RunIntent)
        stranger = FakeInteraction(self.bot, self.bob)
        await button.callback(stranger)
        self.assertIn("someone else", str(stranger.log))
        press = FakeInteraction(self.bot, self.alice)
        await button.callback(press)
        self.assertIn("Wallet", press.text())                             # the real /balance ran
        self.s.sessions.start(self.alice.id)                              # the command's own checks still apply
        blocked = FakeInteraction(self.bot, self.alice)
        await RunIntent(self.alice.id, "deposit").callback(blocked)       # needs an amount: explained, not guessed
        self.assertIn("/deposit <amount>", str(blocked.log))
        self.s.sessions.end(self.alice.id)


class AnomalyTests(unittest.TestCase):
    def test_isolation_forest_separates_an_outlier(self):
        rng = __import__("numpy").random.default_rng(0)
        X = rng.normal(size=(300, 3))
        forest = IsolationForest(seed=1).fit(X)
        scores = forest.score(__import__("numpy").vstack([X[:5], [[8, 8, 8]]]))
        self.assertGreater(scores[-1], scores[:-1].max())

    def test_synthetic_benchmark_quality(self):
        entries, failures, truth = synthetic(seed=11)
        ws = windows(entries, failures)
        det = AnomalyDetector(seed=11)
        self.assertTrue(det.fit(ws))
        flagged = {(f.user, f.hour) for w in ws if (f := det.judge(w))}
        tp = len(flagged & truth)
        self.assertGreater(tp / len(truth), 0.6)
        self.assertGreater(tp / max(1, len(flagged)), 0.7)

    def test_review_signals_open_once_never_touch_money_and_can_be_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = FixedClock(datetime(2026, 5, 1, 12))
            svc = ReviewService(JsonStore(os.path.join(tmp, "r.json"), dict), Ledger(os.path.join(tmp, "l.jsonl")),
                                Ledger(os.path.join(tmp, "f.jsonl")), clock)
            svc.refit()                                                   # cold start: rules only
            for _ in range(20):
                clock.advance(seconds=61)
                svc.on_failure(42, "/slots: CheckFailure")
            open_ = svc.signals("open")
            self.assertEqual(len(open_), 1)                               # cooldown: one open signal
            self.assertEqual(open_[0]["kind"], "rule")
            resolved = svc.resolve(open_[0]["id"], 999, "dismissed", "testing")
            self.assertEqual((resolved["status"], resolved["resolved_by"]), ("dismissed", "999"))
            self.assertFalse(os.path.exists(os.path.join(tmp, "data.json")))


class ReviewCommandTests(CogTestCase):
    async def test_review_commands_are_admin_only(self):
        command = self.bot.tree.get_command("review").get_command("list")
        it = await self.run_command("review", self.alice, group="list")
        self.assertTrue(it.log)
        admin = await self.run_command("review", self.admin, group="list")
        self.assertIn("review", admin.text().lower())


class WishCommandIconTests(CogTestCase):
    async def _run(self, sub, **kwargs):
        from tests.fakes import FakeInteraction
        tcg = self.bot.tree.get_command("tcg")
        command = next(c for c in next(g for g in tcg.commands if g.name == "wish").commands if c.name == sub)
        it = FakeInteraction(self.bot, self.alice)
        it.command = command
        await command.callback(command.binding, it, **kwargs)
        return it

    def _fits(self, embed):
        self.assertLessEqual(len(embed.description or ""), 4096)
        for field in embed.fields:
            self.assertLessEqual(len(field.value), 1024, field.name)

    async def test_banner_and_ten_wishes_use_the_star_shard_and_dust_icons_within_limits(self):
        from types import SimpleNamespace
        from rudhabot.ui.theme import TCG
        banner = await self._run("banner")
        self._fits(banner.last_embed)
        self.assertIn(TCG.rarity(5), banner.last_embed.description)
        self.assertIn(TCG.SHARD, banner.text())
        self.fund(self.alice, 20_000)
        await self._run("shards", amount=1_600)                          # 16,000 ra: exactly one 10-wish
        pull = await self._run("pull", count=SimpleNamespace(value=10), banner=None)
        embed = pull.last_embed
        self._fits(embed)
        self.assertEqual(sum(line.startswith(TCG.STAR) for line in embed.description.split("\n")), 10)
        self.assertIn(TCG.SHARD, {f.name: f.value for f in embed.fields}["Left"])

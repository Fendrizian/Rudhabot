"""Clairvoyance for security: the simulator, the wargame scorer, the hashed network, the serving safety valves
(schema refusal, missing or corrupt artifacts, timeouts), and the review-only model path."""
import asyncio
import json
import os
import shutil
import tempfile
import unittest

import numpy as np

from rudhabot.ml.security.hashing import encode_messages, message_tokens
from rudhabot.ml.security.serving import DEFAULT_DIR, SecurityModels
from rudhabot.ml.security.sim import FAMILIES, Server
from rudhabot.ml.security.sparse import HashedMultiTaskNet
from rudhabot.ml.security.wargame import run_day, sim_config
from rudhabot.security.engine import GuildEngine
from rudhabot.security.events import Message
from rudhabot.security.levels import Level
from rudhabot.security.policy import SecurityConfig


class SimulatorTests(unittest.TestCase):
    def test_every_attack_family_is_generated_and_labelled(self):
        server = Server(3, members=300)
        for family in FAMILIES:
            day = server.simulate_day((family,), normal_events=())
            self.assertEqual([a["family"] for a in day.attacks], [family])
            a = day.attacks[0]
            self.assertLessEqual(a["start"], a["end"])
            self.assertTrue(a["accounts"])
            self.assertEqual(day.events, sorted(day.events, key=lambda e: e.ts))

    def test_the_rules_catch_a_join_raid_in_the_wargame(self):
        server = Server(5, members=400)
        from rudhabot.ml.security.sim import BOT, GUILD
        engine = GuildEngine(GUILD, sim_config(), bot_id=BOT)
        run_day(engine, server.simulate_day(()), collect=False)
        result = run_day(engine, server.simulate_day(("join_raid",), normal_events=()), collect=True)
        attack = result.attacks[0]
        self.assertIsNotNone(attack["alerted_at"])
        self.assertGreater(attack["probation"], 0)
        self.assertEqual(len(result.features), len(result.labels))
        self.assertTrue(any(label == "join_raid" for label in result.labels))


class NetworkTests(unittest.TestCase):
    def test_hashing_is_stable_and_the_network_learns_and_round_trips(self):
        self.assertEqual(encode_messages(["free nitro now"])[0].tolist(), encode_messages(["free nitro now"])[0].tolist())
        self.assertTrue(any(t.startswith("lc:") for t in message_tokens("https://dlscord-gift.com/x")))
        texts = ["claim free nitro at https://dlscord-gift.com/x"] * 40 + ["see you at the senate meeting"] * 40
        idx, off = encode_messages(texts)
        idx = idx % (1 << 12)                                            # a small table for the test
        y = np.zeros((80, 3), np.float32)
        y[:40, 0] = y[:40, 1] = 1
        mask = np.zeros((80, 3), np.float32)
        mask[:, :2] = 1
        net = HashedMultiTaskNet(buckets=1 << 12, dim=8, hidden=(16, 8), seed=1)
        net.fit((idx, off, y, mask, np.ones(80, np.float32)), epochs=12, batch=16, lr=5e-3, log=lambda s: None)
        p = net.predict(idx, off)
        self.assertGreater(p[:40, 0].mean(), 0.8)
        self.assertLess(p[40:, 0].mean(), 0.2)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.npz")
            net.save(path)
            again = HashedMultiTaskNet.load(path)
            self.assertTrue(np.allclose(again.predict(idx, off), p, atol=2e-3))      # float16 table


class ServingTests(unittest.IsolatedAsyncioTestCase):
    def test_shipped_models_load_and_score(self):
        models = SecurityModels(DEFAULT_DIR).load()
        if not os.path.exists(os.path.join(DEFAULT_DIR, "manifest.json")):
            self.skipTest("no trained security models in this checkout")
        self.assertTrue(models.message_ready and models.incident_ready, models.errors)
        scam = models.message_scores("FREE NITRO for everyone, claim it here https://dlscord-gift.com/claim")
        calm = models.message_scores("anyone coming to the senate meeting tonight?")
        self.assertGreater(scam["scam"], calm["scam"])
        self.assertLess(calm["scam"], 0.4)
        from rudhabot.security.features import DIM
        reading = models.incident_scores(np.zeros(DIM, np.float32))
        self.assertIn(reading["family"], ("raid", "spam", "phishing", "nuke", "integration"))

    def test_missing_mismatched_or_corrupt_artifacts_fall_back_to_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(SecurityModels(tmp).load().message_ready)                # no manifest
            if os.path.exists(os.path.join(DEFAULT_DIR, "manifest.json")):
                for name in os.listdir(DEFAULT_DIR):
                    shutil.copy(os.path.join(DEFAULT_DIR, name), tmp)
                with open(os.path.join(tmp, "manifest.json")) as f:
                    manifest = json.load(f)
                manifest["message"]["schema"] = "sechash-v0-old"
                with open(os.path.join(tmp, "incident-v1.npz"), "wb") as f:
                    f.write(b"not a model")
                with open(os.path.join(tmp, "manifest.json"), "w") as f:
                    json.dump(manifest, f)
                models = SecurityModels(tmp).load()
                self.assertFalse(models.message_ready)
                self.assertIn("refused", models.errors["message"])
                self.assertFalse(models.incident_ready)
        self.assertEqual(SecurityModels(DEFAULT_DIR, enabled=False).load().status()["message"],
                         "off (RUDHABOT_SECURITY_MODELS=0)")

    async def test_slow_or_failing_inference_returns_none(self):
        models = SecurityModels("/nonexistent").load()
        models.message = object()                         # pretend a model is loaded

        def slow(text):
            import time
            time.sleep(0.3)
            return {}
        models.message_scores = slow
        import rudhabot.ml.security.serving as serving
        old, serving.TIMEOUT_S = serving.TIMEOUT_S, 0.05
        try:
            self.assertIsNone(await models.score_message("hi"))
            self.assertEqual(models.stats["message_timeout"], 1)
            models.message_scores = lambda text: 1 / 0
            self.assertIsNone(await models.score_message("hi"))
            self.assertEqual(models.stats["message_error"], 1)
        finally:
            serving.TIMEOUT_S = old
            await asyncio.sleep(0.3)


class ReviewPathTests(unittest.TestCase):
    def test_a_corroborated_model_reading_alerts_staff_but_changes_nothing(self):
        t = 1_780_000_000.0
        engine = GuildEngine(1, SecurityConfig(), bot_id=99)
        msg = Message(1, 10, 1, 7, t, content="claim your reward at https://qwerty-portal.com/login",
                      joined_at=t - 86400 * 60, created_at=t - 86400 * 600)
        out = engine.observe_model(msg, scam=0.97, spam=0.5)
        inc = (out.opened or out.updated)[0]
        self.assertGreaterEqual(inc.severity, 0.5)                          # alert-worthy, for review
        self.assertEqual(inc.rule_severity, 0.0)
        self.assertEqual(engine.level, Level.NORMAL)                        # the threat level doesn't move
        self.assertEqual(out.plans, [])                                     # and nothing is done
        fresh = GuildEngine(1, SecurityConfig(), bot_id=99)
        plain = fresh.observe_model(Message(1, 10, 2, 8, t, content="call now to win"), scam=0.97, spam=0.5)
        self.assertLess(((plain.opened or plain.updated)[0]).severity, 0.2)  # no link: evidence only


if __name__ == "__main__":
    unittest.main()

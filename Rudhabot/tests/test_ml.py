"""The Cloudfall Clairvoyance: features, network, artifacts, serving fallbacks, trust boundary and metrics."""
import asyncio
import os
import random
import tempfile
import time
import unittest

import numpy as np

from rudhabot.ml import features as F
from rudhabot.ml.evaluate import auc, brier, ece, log_loss
from rudhabot.ml.nn import ClairvoyanceNet, load_net
from rudhabot.ml.model_store import ArtifactError, ModelStore
from rudhabot.ml.policy import HeuristicEvaluator, choose, determinize
from rudhabot.services.clairvoyance import ClairvoyanceService
from rudhabot.settings import DEFAULT_MODELS_DIR
from rudhabot.tcg import engine as E
from rudhabot.tcg.decks import ARCHETYPES


def midgame(seed=5, steps=60):
    rng = random.Random(seed)
    g = E.new_game([list(ARCHETYPES["storm"]["cards"]), list(ARCHETYPES["blackcloud"]["cards"])], ["A", "B"], seed)
    for _ in range(steps):
        if g.over:
            break
        E.apply(g, rng.choice(E.legal_actions(g)).key)
    return g


def tiny_net(seed=0):
    return ClairvoyanceNet(F.DENSE_DIM, F.N_CARDS, embed=4, h1=8, h2=4, seed=seed)


class FeatureTests(unittest.TestCase):
    def test_shapes_and_schema_hash_are_stable(self):
        dense, bags = F.encode(midgame(), 0)
        self.assertEqual(dense.shape, (F.DENSE_DIM,))
        self.assertEqual(bags.shape, (3, F.N_CARDS))
        self.assertEqual(F.schema_hash(), F.schema_hash())
        self.assertEqual(len(F.schema_hash()), 16)

    def test_features_never_depend_on_hidden_information(self):
        g = midgame()
        before = F.encode(g, 0)
        scrambled = g.clone()
        opp = scrambled.players[1]
        pool = opp.hand + opp.deck
        random.Random(1).shuffle(pool)
        opp.hand, opp.deck = pool[:len(opp.hand)], pool[len(opp.hand):]
        random.Random(2).shuffle(scrambled.players[0].deck)
        after = F.encode(scrambled, 0)
        np.testing.assert_array_equal(before[0], after[0])
        np.testing.assert_array_equal(before[1], after[1])

    def test_determinize_keeps_what_the_player_can_see(self):
        g = midgame()
        d = determinize(g, 0, random.Random(0))
        self.assertEqual(d.players[0].hand, g.players[0].hand)
        self.assertEqual(len(d.players[1].hand), len(g.players[1].hand))
        self.assertEqual(sorted(d.players[1].hand + d.players[1].deck), sorted(g.players[1].hand + g.players[1].deck))
        self.assertEqual(E.public_summary(d), E.public_summary(g))


class NetworkTests(unittest.TestCase):
    def test_training_reduces_loss_on_a_learnable_signal(self):
        rng = np.random.default_rng(0)
        dense = rng.standard_normal((800, F.DENSE_DIM)).astype(np.float32)
        bags = np.zeros((800, 3, F.N_CARDS), np.float32)
        y = (dense[:, 0] > 0).astype(np.float32)
        net = tiny_net()
        history = net.fit((dense, bags), y, epochs=10, batch=64, lr=1e-2, log=lambda *_: None)
        self.assertLess(history[-1]["train_loss"], history[0]["train_loss"])
        self.assertGreater(auc(net.predict(dense, bags), y), 0.9)

    def test_save_load_roundtrip_is_exact_including_calibration(self):
        net = tiny_net()
        net.calibration = (0.8, -0.1)
        net.meta = {"version": "t", "schema": F.schema_hash()}
        g = midgame()
        dense, bags = F.encode(g, 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.npz")
            net.save(path)
            loaded = load_net(path)
        np.testing.assert_allclose(loaded.predict(dense[None], bags[None]), net.predict(dense[None], bags[None]))
        self.assertEqual(loaded.meta["version"], "t")

    def test_platt_calibration_fixes_an_overconfident_model(self):
        rng = np.random.default_rng(1)
        dense = rng.standard_normal((2000, F.DENSE_DIM)).astype(np.float32)
        bags = np.zeros((2000, 3, F.N_CARDS), np.float32)
        net = tiny_net()
        z = net.logits(dense, bags)
        y = (rng.random(2000) < 1 / (1 + np.exp(-z / 4))).astype(np.float32)    # truth is 4x less confident
        before = ece(net.predict(dense, bags), y)
        net.calibrate((dense, bags), y)
        self.assertLess(ece(net.predict(dense, bags), y), before)


class ArtifactTests(unittest.TestCase):
    def test_register_load_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ModelStore(tmp)
            store.register("v1", tiny_net(1), {"metrics": {}})
            store.register("v2", tiny_net(2), {"metrics": {}})
            self.assertEqual(store.load_current().meta["version"], "v2")
            store.set_current("v1")
            self.assertEqual(store.load_current().meta["version"], "v1")

    def test_schema_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ModelStore(tmp)
            store.register("v1", tiny_net(), {})
            manifest = store.manifest()
            manifest["versions"]["v1"]["schema"] = "deadbeef"
            store._write(manifest)
            with self.assertRaises(ArtifactError):
                store.load_current()

    def test_shipped_artifact_matches_the_running_feature_schema(self):
        store = ModelStore(DEFAULT_MODELS_DIR)
        net = store.load_current()
        entry = store.manifest()["versions"][net.meta["version"]]
        self.assertEqual(entry["schema"], F.schema_hash())
        self.assertIn("auc", entry["metrics"].get("model") or entry["metrics"]["oracle"])


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_model_falls_back_to_the_heuristic(self):
        with tempfile.TemporaryDirectory() as tmp:
            clairvoyance = ClairvoyanceService(tmp, rng=random.Random(0))
        self.assertFalse(clairvoyance.ready)
        self.assertEqual(clairvoyance.version, HeuristicEvaluator.name)
        g = midgame()
        key = await clairvoyance.choose_move(g)
        self.assertIn(key, [a.key for a in E.legal_actions(g)])

    async def test_disabled_by_configuration(self):
        clairvoyance = ClairvoyanceService(DEFAULT_MODELS_DIR, enabled=False)
        self.assertFalse(clairvoyance.ready)
        self.assertIn("disabled", clairvoyance.status()["load_error"])

    async def test_model_errors_degrade_to_fallback_not_crash(self):
        clairvoyance = ClairvoyanceService(DEFAULT_MODELS_DIR, rng=random.Random(0))
        clairvoyance.choose_sync = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        g = midgame()
        key = await clairvoyance.choose_move(g)
        self.assertIn(key, [a.key for a in E.legal_actions(g)])
        self.assertEqual(clairvoyance.status()["calls"]["move_fallback"], 1)

    async def test_ai_only_ever_picks_legal_moves_and_does_not_touch_the_game(self):
        clairvoyance = ClairvoyanceService(DEFAULT_MODELS_DIR, rng=random.Random(0))
        self.assertTrue(clairvoyance.ready)
        for seed in range(6):
            g = midgame(seed)
            if g.over:
                continue
            snapshot = E.public_summary(g), list(g.history)
            key = await clairvoyance.choose_move(g, "hard")
            self.assertIn(key, [a.key for a in E.legal_actions(g)])
            self.assertEqual((E.public_summary(g), list(g.history)), snapshot)

    async def test_public_win_probability_reveals_nothing_about_hands(self):
        clairvoyance = ClairvoyanceService(DEFAULT_MODELS_DIR, rng=random.Random(0))
        g = midgame()
        p = clairvoyance.public_win_probability_sync(g, 0)
        swapped = g.clone()
        swapped.players[0].hand, swapped.players[1].hand = list(reversed(g.players[0].hand)), ["A01"] * len(g.players[1].hand)
        self.assertAlmostEqual(p, clairvoyance.public_win_probability_sync(swapped, 0), places=6)
        self.assertTrue(0 <= p <= 1)

    async def test_hints_rank_legal_moves(self):
        clairvoyance = ClairvoyanceService(DEFAULT_MODELS_DIR, rng=random.Random(0))
        g = midgame(8)
        hints = await clairvoyance.hints(g)
        labels = {a.label for a in E.legal_actions(g)}
        self.assertTrue(all(h.label in labels for h in hints))
        self.assertEqual([h.win for h in hints], sorted([h.win for h in hints], reverse=True))

    async def test_latency_is_within_budget(self):
        clairvoyance = ClairvoyanceService(DEFAULT_MODELS_DIR, rng=random.Random(0))
        g = midgame()
        start = time.perf_counter()
        for _ in range(10):
            clairvoyance.choose_sync(g)
        self.assertLess((time.perf_counter() - start) / 10, 0.25)      # measured ~1-2 ms; generous for CI noise

    async def test_similar_cards_and_swaps(self):
        clairvoyance = ClairvoyanceService(DEFAULT_MODELS_DIR, rng=random.Random(0))
        sims = clairvoyance.similar_cards("C22", 3)
        self.assertEqual(len(sims), 3)
        self.assertNotIn("C22", [c for c, _ in sims])
        deck = list(ARCHETYPES["vanguard"]["cards"])
        owned = {c: 3 for c in set(deck) | {"C01", "A07", "E02"}}
        for cut, add, gain in clairvoyance.suggest_swaps(deck, owned):
            trial = list(deck); trial.remove(cut); trial.append(add)
            from rudhabot.tcg.decks import validate_deck
            self.assertEqual(validate_deck(trial), [])
            self.assertGreater(gain, 0)


class MetricTests(unittest.TestCase):
    def test_metrics(self):
        y = np.array([0, 0, 1, 1], dtype=float)
        self.assertEqual(auc(np.array([0.1, 0.2, 0.8, 0.9]), y), 1.0)
        self.assertEqual(auc(np.array([0.9, 0.8, 0.2, 0.1]), y), 0.0)
        self.assertAlmostEqual(auc(np.array([0.5, 0.5, 0.5, 0.5]), y), 0.5)
        self.assertAlmostEqual(brier(np.full(4, 0.5), y), 0.25)
        self.assertAlmostEqual(log_loss(np.full(4, 0.5), y), np.log(2))
        self.assertAlmostEqual(ece(np.array([0.0, 0.0, 1.0, 1.0]), y), 0.0)


if __name__ == "__main__":
    unittest.main()


class SlotNetV2Tests(unittest.TestCase):
    def test_v2_features_never_depend_on_hidden_information(self):
        g = midgame()
        before = F.encode_v2(g, 0)
        scrambled = g.clone()
        opp = scrambled.players[1]
        pool = opp.hand + opp.deck
        random.Random(1).shuffle(pool)
        opp.hand, opp.deck = pool[:len(opp.hand)], pool[len(opp.hand):]
        random.Random(2).shuffle(scrambled.players[0].deck)
        for a, b in zip(before, F.encode_v2(scrambled, 0)):
            np.testing.assert_array_equal(a, b)

    def test_v2_slots_carry_unit_identity_and_attachments(self):
        g = midgame(seed=9, steps=120)
        glob, slots, cids, att, bags = F.encode_v2(g, 0)
        for i, u in enumerate(g.players[0].field):
            self.assertEqual(cids[i], -1 if u is None else F.CARD_INDEX[u.cid])
            if u is not None:
                self.assertEqual(att[i].sum(), len(u.equipment()) + len(u.boons) + len(u.afflictions))

    def test_v2_artifact_roundtrip_and_store_schema(self):
        from rudhabot.ml.model_store import ModelStore
        from rudhabot.ml.nn import ClairvoyanceSlotNet
        net = ClairvoyanceSlotNet(F.GLOB_DIM, F.SLOT_FEATURES, F.N_CARDS, embed=4, slot_hidden=6, h1=8, h2=4, seed=2)
        arrays = [a[None] for a in F.encode_v2(midgame(), 0)]
        with tempfile.TemporaryDirectory() as tmp:
            store = ModelStore(tmp)
            store.register("v2test", net, {})
            loaded = store.load_current()
            self.assertEqual(store.manifest()["versions"]["v2test"]["arch"], "v2")
        np.testing.assert_allclose(loaded.predict(*arrays), net.predict(*arrays), rtol=1e-6)

    def test_td_lambda_returns(self):
        from rudhabot.ml.selfplay import lambda_returns
        self.assertEqual(lambda_returns([None, None], 1.0, 0.7), [1.0, 1.0])
        g = lambda_returns([0.2, None, 0.6], 0.0, 0.5)
        self.assertAlmostEqual(g[2], 0.5 * 0.6 + 0.5 * 0.0)
        self.assertAlmostEqual(g[0], 0.5 * 0.2 + 0.5 * g[1])

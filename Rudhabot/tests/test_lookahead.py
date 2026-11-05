"""Clairvoyance's turn lookahead: it only plays its own decisions in the current turn, only ever chooses legal moves,
reads hidden cards only through determinization, and the trusted apply it uses on clones matches the checked one."""
import random
import unittest

import numpy as np

from rudhabot.cogs.tcg import _hint_line
from rudhabot.ml import features as F
from rudhabot.ml import policy as P
from rudhabot.ml.policy import HeuristicEvaluator, Lookahead, plan_actions
from rudhabot.services.clairvoyance import ClairvoyanceService, Hint
from rudhabot.settings import DEFAULT_MODELS_DIR
from rudhabot.tcg import engine as E
from tests.test_ml import midgame


def positions(n=8, steps=40):
    out = []
    for seed in range(n):
        g = midgame(seed, steps)
        if not g.over and len(E.legal_actions(g)) > 1:
            out.append(g)
    return out


class PlanTests(unittest.TestCase):
    def test_every_legal_move_is_ranked_once_and_the_best_are_searched(self):
        for g in positions():
            plans = plan_actions(g, HeuristicEvaluator(), random.Random(0), Lookahead(top=3, max_steps=6))
            legal = [a.key for a in E.legal_actions(g)]
            self.assertEqual(sorted(p.act.key for p in plans), sorted(legal))
            searched = [p.searched for p in plans]
            self.assertEqual(searched, [True] * min(3, len(legal)) + [False] * (len(legal) - min(3, len(legal))))
            self.assertTrue(all(0.0 <= p.value <= 1.0 for p in plans))
            self.assertTrue(all(len(p.line) <= 6 for p in plans))
            self.assertTrue(all(not p.line for p in plans if not p.searched))
            top = [p.value for p in plans if p.searched]
            self.assertEqual(top, sorted(top, reverse=True))

    def test_lines_only_expand_the_deciders_own_turn(self):
        seen = []
        original = P._children

        def spy(base, acts):
            seen.append((E.decider(base), base.active, base.round))
            return original(base, acts)

        P._children = spy
        try:
            for g in positions():
                seen.clear()
                me, active, rnd = E.decider(g), g.active, g.round
                plan_actions(g, HeuristicEvaluator(), random.Random(1), Lookahead(top=4, max_steps=8))
                self.assertTrue(seen)
                self.assertTrue(all(s == (me, active, rnd) for s in seen), seen)
        finally:
            P._children = original

    def test_hidden_cards_are_only_read_through_determinization(self):
        """With determinization replaced by a fixed sample, two games that differ only in hidden zones plan alike."""
        original = P.determinize

        def fixed_sample(g, me, rng):
            c = g.clone()
            c.rng = random.Random(42)
            opp = c.players[c.other(me)]
            pool = sorted(opp.hand + opp.deck)
            opp.hand, opp.deck = pool[:len(opp.hand)], pool[len(opp.hand):]
            c.players[me].deck = sorted(c.players[me].deck)
            return c

        P.determinize = fixed_sample
        try:
            for g in positions(6):
                other = g.clone()
                opp = other.players[other.other(E.decider(g))]
                pool = opp.hand + opp.deck
                random.Random(3).shuffle(pool)
                opp.hand, opp.deck = pool[:len(opp.hand)], pool[len(opp.hand):]
                random.Random(4).shuffle(other.players[E.decider(g)].deck)
                a = plan_actions(g, HeuristicEvaluator(), random.Random(0), Lookahead(top=4))
                b = plan_actions(other, HeuristicEvaluator(), random.Random(0), Lookahead(top=4))
                self.assertEqual([(p.act.key, round(p.value, 9), p.line) for p in a],
                                 [(p.act.key, round(p.value, 9), p.line) for p in b])
        finally:
            P.determinize = original

    def test_hints_never_show_moves_that_depend_on_sampled_deck_cards(self):
        g = positions(1)[0]
        me = E.decider(g)
        size = len(g.players[me].deck)
        play = E.Act("c:A01:u3", "Play Beam Barrage on X", None)
        self.assertTrue(P._shown(g, play, me, size))
        self.assertFalse(P._shown(g, E.Act("p:look_take:t0", "Take Storm Caller", 0), me, size))
        drawn = g.clone()
        drawn.players[me].deck.pop()                                      # the line drew a (sampled) card
        self.assertFalse(P._shown(drawn, play, me, size))

    def test_planning_does_not_touch_the_real_game(self):
        for g in positions(4):
            before = (E.public_summary(g), list(g.history), list(g.players[0].deck), list(g.players[1].hand))
            plan_actions(g, HeuristicEvaluator(), random.Random(0), Lookahead(top=5, samples=2))
            self.assertEqual((E.public_summary(g), list(g.history), list(g.players[0].deck), list(g.players[1].hand)),
                             before)

    def test_zero_lookahead_is_plain_one_ply(self):
        g = positions(1)[0]
        flat = plan_actions(g, HeuristicEvaluator(), random.Random(5), Lookahead(top=0, max_steps=0))
        ranked = P.score_actions(g, HeuristicEvaluator(), random.Random(5))
        self.assertEqual([p.act.key for p in flat], [a.key for a, _ in ranked])


class RecalibrationTests(unittest.TestCase):
    def test_rescaling_the_blend_weight_keeps_every_move_choice(self):
        """recalibrate.py multiplies the blend weight by a_new / a_old, so a new Platt scale changes the probabilities
        shown to people but not one of the AI's choices (lookahead included)."""
        from rudhabot.ml.policy import BlendEvaluator
        from tests.test_ml import tiny_net
        old, new = tiny_net(seed=3), tiny_net(seed=3)
        old.calibration, new.calibration = (0.63, -0.05), (0.80, 0.11)
        w_old = 0.25
        w_new = w_old * new.calibration[0] / old.calibration[0]
        for g in positions(5):
            a = plan_actions(g, BlendEvaluator(old, w_old), random.Random(2), Lookahead(top=3))
            b = plan_actions(g, BlendEvaluator(new, w_new), random.Random(2), Lookahead(top=3))
            self.assertEqual([(p.act.key, p.line) for p in a], [(p.act.key, p.line) for p in b])


class TrustedApplyTests(unittest.TestCase):
    def test_trusted_apply_matches_the_checked_apply(self):
        for g in positions(6, steps=50):
            for act in E.legal_actions(g):
                checked, trusted = g.clone(), g.clone()
                E.apply(checked, act.key)
                E.apply_trusted(trusted, act)
                self.assertEqual(E.public_summary(checked), E.public_summary(trusted))
                self.assertEqual(checked.rng.getstate(), trusted.rng.getstate())
                for seat in (0, 1):
                    for x, y in zip(F.encode_v2(checked, seat), F.encode_v2(trusted, seat)):
                        np.testing.assert_array_equal(x, y)

    def test_the_checked_apply_still_refuses_illegal_moves(self):
        g = positions(1)[0]
        with self.assertRaises(E.IllegalAction):
            E.apply(g, "not-a-move")


class ServiceLookaheadTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_seats_and_hints_use_the_lookahead(self):
        clairvoyance = ClairvoyanceService(DEFAULT_MODELS_DIR, rng=random.Random(0))
        self.assertTrue(clairvoyance.ready)
        self.assertIsNotNone(clairvoyance.lookahead)
        self.assertIn("lookahead", clairvoyance.status()["search"])
        for g in positions(4):
            legal = {a.key for a in E.legal_actions(g)}
            for difficulty in ("easy", "normal", "hard"):
                self.assertIn(await clairvoyance.choose_move(g, difficulty), legal)
            hints = await clairvoyance.hints(g)
            self.assertTrue(all(isinstance(h.line, tuple) and all(isinstance(m, str) for m in h.line) for h in hints))
            self.assertEqual([h.win for h in hints], sorted([h.win for h in hints], reverse=True))

    def test_a_hint_shows_the_planned_line(self):
        text = _hint_line(Hint("Play Storm Caller into slot 1", 0.64, 0.05, ("Go to the Clash", "Attack with Storm Caller")))
        self.assertIn("win 64% (+5)", text)
        self.assertIn("then: Go to the Clash → Attack with Storm Caller", text)
        self.assertNotIn("then", _hint_line(Hint("Go to the Clash", 0.5, 0.0)))


if __name__ == "__main__":
    unittest.main()

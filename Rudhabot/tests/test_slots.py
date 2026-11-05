import random
import unittest
from fractions import Fraction

from rudhabot.domain.slots import DEFAULT_SYMBOLS, SlotKind, SlotMachine
from tests.legacy_slots_reference import SYMBOLS, WEIGHTS, legacy_spin


def new_multiplier(legacy_multiplier):
    """The original paid x2 for a pair (a 124.8% return); pairs now pay x1.4. Triples and misses are unchanged."""
    return Fraction(7, 5) if legacy_multiplier == 2 else legacy_multiplier


class SlotRulesTests(unittest.TestCase):
    """Reels, symbols, weights and RNG usage still equal the original; only the pair payout changed (x2 -> x1.4)."""

    def test_symbol_table_matches_the_original(self):
        self.assertEqual([s.emoji for s in DEFAULT_SYMBOLS], SYMBOLS)
        self.assertEqual([s.weight for s in DEFAULT_SYMBOLS], WEIGHTS)

    def test_identical_reels_and_payouts_for_5000_seeds(self):
        for seed in range(5000):
            legacy_rng, new_rng = random.Random(seed), random.Random(seed)
            legacy_reels, legacy_multiplier = legacy_spin(legacy_rng)
            machine = SlotMachine(rng=new_rng)
            reels = [machine.spin_reel() for _ in range(SlotMachine.REELS)]
            outcome = machine.evaluate(reels)
            self.assertEqual([s.emoji for s in reels], legacy_reels, f"seed {seed}")
            self.assertEqual(outcome.multiplier, new_multiplier(legacy_multiplier), f"seed {seed}")
            # same number of RNG draws consumed => every later random event in the bot replays identically
            self.assertEqual(legacy_rng.getstate(), new_rng.getstate(), f"seed {seed}")

    def test_every_possible_combination_pays_like_the_original(self):
        machine = SlotMachine()
        from itertools import product
        by_emoji = {s.emoji: s for s in DEFAULT_SYMBOLS}
        for combo in product(SYMBOLS, repeat=3):
            _, expected = self._legacy_evaluate(list(combo))
            self.assertEqual(machine.evaluate([by_emoji[e] for e in combo]).multiplier, new_multiplier(expected), combo)

    @staticmethod
    def _legacy_evaluate(result):
        class Fixed:
            def __init__(self, seq): self.seq = list(seq)
            def choices(self, *a, **k): return [self.seq.pop(0)]
        return legacy_spin(Fixed(result))

    def test_exact_return_to_player_and_hit_rate(self):
        machine = SlotMachine()
        # the house keeps about 5% (the original x2 pair returned 124.753%: a money printer)
        self.assertEqual(machine.return_to_player(), Fraction(475589, 500000))      # 95.12%
        self.assertLess(machine.return_to_player(), 1)
        self.assertEqual(machine.hit_rate(), Fraction(6851, 12500))        # 54.808% of spins pay out

    def test_outcome_money_math(self):
        machine = SlotMachine()
        seven = DEFAULT_SYMBOLS[-1]
        triple = machine.evaluate([seven, seven, seven])
        self.assertEqual((triple.kind, triple.multiplier, triple.payout(10), triple.net(10)), (SlotKind.TRIPLE, 50, 500, 490))
        pair = machine.evaluate([seven, DEFAULT_SYMBOLS[0], seven])
        self.assertEqual((pair.kind, pair.symbol, pair.payout(10), pair.net(10), pair.label), (SlotKind.PAIR, seven, 14, 4, "1.4"))
        self.assertEqual(pair.payout(3), 4)                                            # rounded down
        miss = machine.evaluate([DEFAULT_SYMBOLS[0], DEFAULT_SYMBOLS[1], DEFAULT_SYMBOLS[2]])
        self.assertEqual((miss.kind, miss.net(10)), (SlotKind.MISS, -10))

import random
import unittest
from fractions import Fraction

from rudhabot.domain.lottery import (
    DEFAULT_SPECS, DENOMINATOR, GOLDEN_TICKET, MAX_JACKPOT_PROBABILITY, REGULAR_TICKET,
    Lottery, PrizeTable, PrizeTier, TicketSpec,
)
from rudhabot.services.lottery import LotteryService, TicketRefused
from tests.helpers import EnvTestCase


class RequirementTests(unittest.TestCase):
    """The product requirements, stated as executable facts."""

    def test_jackpots(self):
        self.assertEqual(REGULAR_TICKET.jackpot, 1_000_000)
        self.assertEqual(GOLDEN_TICKET.jackpot, 100_000_000)

    def test_smallest_winnings(self):
        self.assertEqual(REGULAR_TICKET.minimum_prize, 10)
        self.assertEqual(GOLDEN_TICKET.minimum_prize, 100)

    def test_jackpot_is_rarer_than_0_005_percent(self):
        for spec in DEFAULT_SPECS:
            self.assertLess(spec.jackpot_probability, Fraction(5, 100_000), spec.label)
            self.assertLess(spec.jackpot_probability, MAX_JACKPOT_PROBABILITY)

    def test_more_zonks_than_wins(self):
        for spec in DEFAULT_SPECS:
            self.assertGreater(spec.table.zonk_probability, spec.table.win_probability, spec.label)
            self.assertGreater(spec.table.zonk_probability, Fraction(1, 2))

    def test_bigger_prizes_are_strictly_rarer(self):
        for spec in DEFAULT_SPECS:
            weights = [t.weight for t in spec.table.tiers]
            self.assertEqual(weights, sorted(set(weights), reverse=True), spec.label)

    def test_probabilities_are_exact_and_sum_to_one(self):
        for spec in DEFAULT_SPECS:
            t = spec.table
            total = sum(t.probability(x) for x in t.tiers) + t.zonk_probability
            self.assertEqual(total, 1)
            self.assertEqual(t.denominator, DENOMINATOR)

    def test_tickets_are_a_money_sink(self):
        for spec in DEFAULT_SPECS:
            self.assertLess(spec.return_to_player, 1)
            self.assertGreater(spec.return_to_player, Fraction(3, 4))     # but not punishing


class ValidationTests(unittest.TestCase):
    def _table(self, weights, amounts=(10, 20, 50)):
        return PrizeTable([PrizeTier(a, w) for a, w in zip(amounts, weights)])

    def test_rejects_a_jackpot_that_is_too_likely(self):
        with self.assertRaises(ValueError):
            TicketSpec("x", "X", 100, self._table([2_000_000, 1_000_000, 600]))         # 0.006%

    def test_rejects_a_table_where_wins_outnumber_zonks(self):
        with self.assertRaises(ValueError):
            TicketSpec("x", "X", 100, self._table([4_000_000, 3_000_000, 200]))

    def test_rejects_rarer_prizes_that_are_not_rarer(self):
        with self.assertRaises(ValueError):
            TicketSpec("x", "X", 100, self._table([1_000_000, 2_000_000, 100]))

    def test_rejects_tables_without_room_for_zonk(self):
        with self.assertRaises(ValueError):
            PrizeTable([PrizeTier(10, DENOMINATOR)])

    def test_rejects_unsorted_or_duplicate_amounts(self):
        with self.assertRaises(ValueError):
            PrizeTable([PrizeTier(20, 5), PrizeTier(10, 3)])
        with self.assertRaises(ValueError):
            PrizeTable([PrizeTier(10, 5), PrizeTier(10, 3)])


class DrawingTests(unittest.TestCase):
    def test_roll_boundaries_map_to_the_right_tier(self):
        table = REGULAR_TICKET.table
        class Roll:
            def __init__(self, n): self.n = n
            def randrange(self, _): return self.n
        self.assertEqual(table.draw(Roll(0)).amount, 10)                              # first slot of first tier
        first = table.tiers[0].weight
        self.assertEqual(table.draw(Roll(first - 1)).amount, 10)
        self.assertEqual(table.draw(Roll(first)).amount, 20)                          # boundary rolls over
        self.assertEqual(table.draw(Roll(table.win_weight - 1)).amount, 1_000_000)    # very last winning slot = jackpot
        self.assertIsNone(table.draw(Roll(table.win_weight)))                         # first zonk slot
        self.assertIsNone(table.draw(Roll(DENOMINATOR - 1)))

    def test_seeded_draws_are_reproducible(self):
        a = Lottery(rng=random.Random(7)).draw_many("golden lottery ticket", 500)
        b = Lottery(rng=random.Random(7)).draw_many("golden lottery ticket", 500)
        self.assertEqual((a.wins, a.zonks), (b.wins, b.zonks))

    def test_empirical_frequencies_match_the_table(self):
        n = 200_000
        batch = Lottery(rng=random.Random(2024)).draw_many("regular lottery ticket", n)
        table = REGULAR_TICKET.table
        self.assertAlmostEqual(batch.zonks / n, float(table.zonk_probability), delta=0.005)
        self.assertAlmostEqual(batch.winning_tickets / n, float(table.win_probability), delta=0.005)
        smallest = batch.wins[10] / n
        self.assertAlmostEqual(smallest, float(table.probability(table.tiers[0])), delta=0.004)
        self.assertEqual(batch.tickets, n)
        self.assertEqual(batch.zonks + batch.winning_tickets, n)
        # empirical return to player within a few standard errors of the exact one
        stderr = float(table.variance()) ** 0.5 / n ** 0.5
        self.assertAlmostEqual(batch.total_won / n, float(table.expected_value()), delta=4 * stderr)

    def test_a_forced_jackpot_is_flagged(self):
        class Always:
            def randrange(self, _): return REGULAR_TICKET.table.win_weight - 1
        draw = Lottery(rng=Always()).draw("regular lottery ticket")
        self.assertEqual((draw.amount, draw.jackpot), (1_000_000, True))


class LotteryServiceTests(EnvTestCase):
    def service(self, rng=None):
        return LotteryService(self.env.users, self.env.ledger, Lottery(rng=rng or random.Random(1)), self.env.clock)

    def test_draw_consumes_tickets_and_pays_the_wallet(self):
        self.env.users.update(1, wallet=1000, inventory={"regular lottery ticket": 5, "boba": 1})
        result = self.service().draw(1, "regular lottery ticket", 3)
        account = self.env.users.get(1)
        self.assertEqual(account["inventory"], {"regular lottery ticket": 2, "boba": 1})
        self.assertEqual(account["wallet"], 1000 + result.batch.total_won)
        self.assertEqual(result.wallet_after, account["wallet"])
        self.assertEqual(result.tickets_left, 2)
        self.assertEqual(result.batch.zonks + result.batch.winning_tickets, 3)

    def test_using_the_last_ticket_removes_the_inventory_entry(self):
        self.env.users.update(1, inventory={"golden lottery ticket": 1})
        self.service().draw(1, "golden lottery ticket", 1)
        self.assertNotIn("golden lottery ticket", self.env.users.get(1)["inventory"])

    def test_refuses_without_enough_tickets_and_changes_nothing(self):
        self.env.users.update(1, wallet=50, inventory={"regular lottery ticket": 1})
        with self.assertRaises(TicketRefused) as ctx:
            self.service().draw(1, "regular lottery ticket", 2)
        self.assertEqual(ctx.exception.code, "not_enough_tickets")
        self.assertEqual(self.env.users.get(1)["inventory"], {"regular lottery ticket": 1})
        self.assertEqual(self.env.users.get(1)["wallet"], 50)

    def test_refuses_non_tickets_and_bad_quantities(self):
        self.env.users.update(1, inventory={"boba": 1, "regular lottery ticket": 1})
        for item, qty, code in [("boba", 1, "not_a_ticket"), ("regular lottery ticket", 0, "not_positive")]:
            with self.assertRaises(TicketRefused) as ctx:
                self.service().draw(1, item, qty)
            self.assertEqual(ctx.exception.code, code)

    def test_jackpot_is_paid_and_recorded(self):
        class Always:
            def randrange(self, _): return GOLDEN_TICKET.table.win_weight - 1
        self.env.users.update(7, inventory={"golden lottery ticket": 1})
        result = self.service(Always()).draw(7, "golden lottery ticket", 1)
        self.assertTrue(result.jackpot_hit)
        self.assertEqual(self.env.users.get(7)["wallet"], 100_000_000)
        wins = self.env.ledger.recent_jackpots()
        self.assertEqual([(w["user_id"], w["amount"]) for w in wins], [("7", 100_000_000)])
        stats = self.env.ledger.stats()["golden lottery ticket"]
        self.assertEqual((stats["tickets"], stats["paid"], stats["spent"]), (1, 100_000_000, 1000))

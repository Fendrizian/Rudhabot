"""Fortune cookies: a few hundred unique fortunes, drawn uniformly at random, one per cookie."""
import random
import unittest
from collections import Counter

from rudhabot.domain.fortunes import FORTUNES, crack
from tests.cog_case import CogTestCase


class FortuneListTests(unittest.TestCase):
    def test_a_few_hundred_unique_short_fortunes(self):
        self.assertGreaterEqual(len(FORTUNES), 300)
        self.assertEqual(len(set(FORTUNES)), len(FORTUNES))
        self.assertTrue(all(0 < len(f) <= 120 for f in FORTUNES))

    def test_draws_are_uniform(self):
        rng = random.Random(42)
        n = len(FORTUNES) * 200
        counts = Counter(crack(rng) for _ in range(n))
        self.assertEqual(set(counts), set(FORTUNES))                       # every fortune can appear
        expected = n / len(FORTUNES)
        chi2 = sum((counts[f] - expected) ** 2 / expected for f in FORTUNES)
        # df = len(FORTUNES) - 1 ≈ 314: a uniform draw stays far below 314 + 4·√(2·314) ≈ 414
        self.assertLess(chi2, len(FORTUNES) - 1 + 4 * (2 * (len(FORTUNES) - 1)) ** 0.5)


class FortuneCookieCommandTests(CogTestCase):
    async def test_eating_a_cookie_reveals_a_fortune(self):
        self.fund(self.alice, 0, inventory={"fortune cookie": 2})
        it = await self.run_command("use", self.alice, item="fortune cookie")
        embed = it.last_embed
        self.assertIn("Your fortune", embed.title)
        self.assertTrue(any(f in embed.description for f in FORTUNES))
        self.assertEqual(self.account(self.alice)["inventory"]["fortune cookie"], 1)

    async def test_many_cookies_list_ten_fortunes_and_summarise_the_rest(self):
        self.fund(self.alice, 0, inventory={"fortune cookie": 15})
        it = await self.run_command("use", self.alice, item="cookie", quantity=15)
        lines = it.last_embed.description.split("\n")
        self.assertEqual(sum(1 for line in lines if line.startswith("🥠")), 10)
        self.assertIn("5 more", lines[-1])
        self.assertNotIn("fortune cookie", self.account(self.alice)["inventory"])

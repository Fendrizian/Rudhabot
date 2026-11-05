"""Randomised /work and /crime replies, and /rob pinging its target (a line above the embed, since embeds never ping)."""
import unittest

import discord

from rudhabot.ui.flavour import CRIME_CAUGHT, CRIME_SUCCESS, WORK, money
from rudhabot.ui.theme import CURRENCY
from tests.cog_case import CogTestCase


class MessageListTests(unittest.TestCase):
    def test_at_least_fifty_lines_each_with_one_placeholder_and_no_repeats(self):
        for name, lines, placeholder in (("work", WORK, "{amount}"), ("crime", CRIME_SUCCESS, "{amount}"),
                                         ("caught", CRIME_CAUGHT, "{fine}")):
            self.assertGreaterEqual(len(lines), 50, name)
            self.assertEqual(len(set(lines)), len(lines), name)
            for line in lines:
                self.assertEqual(line.count(placeholder), 1, line)
                self.assertNotIn("{" + ("fine" if placeholder == "{amount}" else "amount") + "}", line)

    def test_the_servers_own_work_lines_are_kept_as_written(self):
        self.assertEqual(WORK[0], "You worked as a factory worker in Baruwarnapura and earned {amount}")
        self.assertIn("You worked at Subway and got paid {amount}", WORK)
        self.assertIn("You somehow traveled to the year 1940, and you were hired as a bomber! Good thing you came back "
                      "to your current time with the {amount}. But be thankful you were alive, ya little weiner.", WORK)
        self.assertEqual(len([w for w in WORK[:40]]), 40)


class FlavourCommandTests(CogTestCase):
    async def test_work_and_crime_use_a_random_line_with_the_amount(self):
        self.script.rolls = [17]
        it = await self.run_command("work", self.alice)
        text = it.last_embed.description
        self.assertTrue(any(text == "💼 " + w.format(amount=money(17)) for w in WORK), text)
        self.assertEqual(self.account(self.alice)["wallet"], 17)
        self.script.rolls = [10, 40]                                   # a success roll, then 40 ra
        it = await self.run_command("crime", self.alice)
        self.assertTrue(any(it.last_embed.description == "🚬 " + c.format(amount=money(40)) for c in CRIME_SUCCESS))
        self.clock.advance(minutes=31)
        self.fund(self.alice, 1000)
        self.script.rolls = [90, 10]                                   # caught, fined 10%
        it = await self.run_command("crime", self.alice)
        self.assertTrue(any(it.last_embed.description == "🚨 " + c.format(fine=money(100)) for c in CRIME_CAUGHT))
        self.assertIn(f"**{CURRENCY}100**", it.last_embed.description)

    def _only_target(self, entry, target):
        mentions = entry["allowed_mentions"]
        self.assertIsInstance(mentions, discord.AllowedMentions)
        self.assertEqual([u.id for u in mentions.users], [target.id])
        self.assertFalse(mentions.everyone)

    async def test_rob_pings_the_target_when_it_works_and_when_it_fails(self):
        self.fund(self.bob, 1000)
        self.fund(self.alice, 500)
        self.script.choice = lambda seq: seq[0]                         # the robbery succeeds
        self.script.rolls = [60]
        it = await self.run_command("rob", self.alice, target=self.bob)
        entry = it.log[0][1]
        self.assertIn(self.bob.mention, entry["content"])
        self.assertIn("robbed by", entry["content"])
        self._only_target(entry, self.bob)
        self.assertEqual(self.account(self.bob)["wallet"], 400)
        self.clock.advance(minutes=16)
        self.script.choice = lambda seq: seq[-1]                        # caught
        self.script.rolls = [10]
        it = await self.run_command("rob", self.alice, target=self.bob)
        entry = it.log[0][1]
        self.assertIn("tried to rob you but got caught", entry["content"])
        self._only_target(entry, self.bob)

    async def test_no_ping_when_there_was_nothing_to_rob(self):
        self.script.choice = lambda seq: seq[0]
        it = await self.run_command("rob", self.alice, target=self.bob)          # bob's wallet is empty
        self.assertNotIn("content", it.log[0][1])
        self.assertIn("doesn't have enough money", it.last_embed.description)

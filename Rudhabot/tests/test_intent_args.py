"""Clairvoyance intent v3: reading arguments out of a message, the pre-filled /ask card, and the new intents."""
import os
import unittest

from rudhabot.cogs.assistant import prefilled, suggestion
from rudhabot.ml.intent import MODEL_FILE, IntentRouter, Route
from rudhabot.ml.intent_args import extract, usage
from rudhabot.ml.intent_data import INTENTS, TEST_3, TRAIN
from rudhabot.settings import DEFAULT_MODELS_DIR
from tests.cog_case import CogTestCase

COIN = [("bet", []), ("choice", [("Heads", "heads"), ("Tails", "tails")])]
RPS = [("bet", []), ("choice", [("Rock", "rock"), ("Paper", "paper"), ("Scissors", "scissors")])]
CRAPS = [("bet", []), ("play", [("Pass Line (1:1)", "pass"), ("Don't Pass (1:1, 12 pushes)", "dontpass"),
                                ("Field (one roll: 2 pays 2:1, 12 pays 3:1)", "field")])]
DICE = [("bet", []), ("guess", [])]
SEND = [("target", []), ("amount", [])]


class ExtractTests(unittest.TestCase):
    def test_amounts_choices_guesses_and_mentions(self):
        cases = [
            ("flip a coin for 100 on heads", COIN, {"bet": "100", "choice": "heads"}),
            ("coinflip all on tails", COIN, {"bet": "all", "choice": "tails"}),
            ("rps 2.5k scissors", RPS, {"bet": "2500", "choice": "scissors"}),
            ("rock paper scissors", RPS, {}),                       # two different choices: none was picked
            ("craps pass line 1,000", CRAPS, {"bet": "1000", "play": "pass"}),
            ("dont pass 50", CRAPS, {"bet": "50", "play": "dontpass"}),
            ("don’t pass for 50", CRAPS, {"bet": "50", "play": "dontpass"}),
            ("dice 50 on 7", DICE, {"bet": "50", "guess": "7"}),
            ("roll a dice and guess 4", DICE, {"guess": "4"}),
            ("100 on 8 dice", DICE, {"bet": "100", "guess": "8"}),
            ("send 1m to <@123456789012345678>", SEND, {"target": "<@123456789012345678>", "amount": "1000000"}),
            ("give some money to my friend", SEND, {}),
        ]
        for text, params, expected in cases:
            self.assertEqual(extract(text, params), expected, text)

    def test_a_guess_out_of_range_is_not_used(self):
        self.assertEqual(extract("dice 30 on 13", DICE), {"bet": "30"})

    def test_items_are_read_with_the_catalogue(self):
        known = {"mystery box": "mystery box", "hoodie": "fiona hoodie"}
        found = extract("open my mystery box please", [("item", []), ("quantity", [])], known.get)
        self.assertEqual(found, {"item": "mystery box"})
        self.assertEqual(extract("sell it", [("item", [])], known.get), {})

    def test_usage_shows_found_values_and_marks_missing_required_ones(self):
        self.assertEqual(usage("coinflip", [("bet", True), ("choice", True)], {"bet": "100"}),
                         "/coinflip bet:100 <choice>")
        self.assertEqual(usage("sell", [("item", True), ("quantity", False)], {"item": "cap"}), "/sell item:cap")


class DataTests(unittest.TestCase):
    def test_new_intents_have_training_and_held_out_phrases(self):
        new = ("guide", "collect", "income", "send", "rob", "sell", "use", "history", "slots", "roulette", "crash",
               "highlow", "coinflip", "dice", "rps")
        for intent in new:
            self.assertIn(intent, INTENTS)
            self.assertGreaterEqual(len(TRAIN[intent]), 10, intent)
            self.assertGreaterEqual(len(TEST_3[intent]), 4, intent)

    def test_no_test_phrase_is_trained_on(self):
        from rudhabot.ml.intent import normalise
        from rudhabot.ml.intent_data import TEST, TEST_2
        trained = {normalise(t) for v in TRAIN.values() for t in v}
        for test in (TEST, TEST_2, TEST_3):
            for phrases in test.values():
                for phrase in phrases:
                    self.assertNotIn(normalise(phrase), trained, phrase)


class StubRouter:
    confident = 0.8

    def __init__(self, intent, confidence=0.97):
        self.intent, self.confidence, self.recorded = intent, confidence, []

    def route(self, text):
        return Route(self.intent, self.confidence, ())

    def record(self, kind, intent=None):
        self.recorded.append((kind, intent))


class PrefilledCardTests(CogTestCase):
    def test_prefilled_uses_the_real_command_parameters(self):
        tree = self.bot.tree
        self.assertEqual(prefilled(tree, "coinflip", "flip a coin for 100 on heads"), "/coinflip bet:100 choice:heads")
        self.assertEqual(prefilled(tree, "craps", "50 on don't pass"), "/craps bet:50 play:dontpass")
        self.assertIsNone(prefilled(tree, "coinflip", "i want to flip a coin"))       # nothing read: no template
        self.assertIsNone(prefilled(tree, "balance", "my balance 100"))               # takes no arguments we read

    def test_the_card_shows_the_command_to_type_and_never_runs_it(self):
        embed, view = suggestion(StubRouter("coinflip"), self.alice.id, "flip a coin for 100 on heads", self.bot.tree)
        self.assertIn("Type `/coinflip bet:100 choice:heads` to do it.", embed.description)
        self.assertEqual(self.account(self.alice)["wallet"], 0)                       # nothing was bet
        self.assertTrue(view.children[0].item.label.startswith("How to"))
        embed, _ = suggestion(StubRouter("balance"), self.alice.id, "show my balance", self.bot.tree)
        self.assertNotIn("Type `", embed.description)                                # runnable: the button runs it


@unittest.skipUnless(os.path.exists(os.path.join(DEFAULT_MODELS_DIR, MODEL_FILE)), "intent model not built")
class ShippedRouterTests(unittest.TestCase):
    def test_the_shipped_router_knows_the_new_commands(self):
        router = IntentRouter(os.path.join(DEFAULT_MODELS_DIR, MODEL_FILE))
        self.assertTrue(router.ready)
        for text, intent in [("collect my role income", "collect"), ("spin the slots", "slots"),
                             ("show me the guidebook", "guide"), ("send money to a friend", "send"),
                             ("bank history", "history"), ("hello there", "chat")]:
            self.assertEqual(router.route(text).intent, intent, text)


if __name__ == "__main__":
    unittest.main()

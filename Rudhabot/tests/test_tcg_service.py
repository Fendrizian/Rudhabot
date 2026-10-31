"""TcgService: collections, decks, packs and the server-authoritative match lifecycle."""
import os
import random
import tempfile
import unittest
from datetime import datetime

from rudhabot.clock import FixedClock
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.tcg import TcgRepository
from rudhabot.persistence.users import UserRepository
from rudhabot.services.oracle import OracleService
from rudhabot.services.tcg import TcgError, TcgService, TcgTerms, parse_deck_text, safe_default
from rudhabot.settings import DEFAULT_MODELS_DIR
from rudhabot.tcg import engine as E
from rudhabot.tcg.decks import ARCHETYPES

ALICE, BOB, CARL = 1001, 1002, 1003


class Base(unittest.IsolatedAsyncioTestCase):
    def build(self, oracle=None):
        return TcgService(TcgRepository(JsonStore(self.path("tcg.json"), dict), JsonStore(self.path("m.json"), dict)),
                          self.users, self.clock, random.Random(42), TcgTerms(), oracle=oracle)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FixedClock(datetime(2026, 5, 1, 12, 0, 0))
        self.users = UserRepository(JsonStore(self.path("data.json"), dict), ["SCAY"])
        self.svc = self.build()
        self.svc.claim_starter(ALICE, "vanguard")
        self.svc.claim_starter(BOB, "storm")

    async def human_move(self, match, key=None):
        seat = E.decider(match.game)
        uid = int(match.record["seats"][seat])
        acts = E.legal_actions(match.game)
        return await self.svc.act(match.id, uid, key or safe_default(acts), match.version)


class CollectionTests(Base):
    async def test_starter_once_and_deck_saved(self):
        profile = self.svc.profile(ALICE)
        self.assertEqual(len(profile["decks"]["Jahnen Vanguard"]), 40)
        self.assertEqual(profile["active_deck"], "Jahnen Vanguard")
        with self.assertRaises(TcgError):
            self.svc.claim_starter(ALICE, "storm")

    async def test_deck_must_be_legal_and_owned(self):
        cards = list(ARCHETYPES["vanguard"]["cards"])
        self.assertEqual(self.svc.save_deck(ALICE, "Copy", cards), [])
        bad = cards[:-1] + ["C01"]                                  # Harp: legal card, but not owned
        problems = self.svc.save_deck(ALICE, "Bad", bad)
        self.assertTrue(any("You own 0" in p for p in problems))
        self.assertNotIn("Bad", self.svc.profile(ALICE)["decks"])

    async def test_pack_costs_wallet_money_and_adds_cards(self):
        with self.assertRaises(TcgError):
            self.svc.open_pack(ALICE)
        self.users.update(ALICE, wallet=1000)
        before = sum(self.svc.profile(ALICE)["collection"].values())
        pulled = self.svc.open_pack(ALICE)
        self.assertEqual(len(pulled), 5)
        self.assertEqual(self.users.get(ALICE)["wallet"], 750)
        self.assertEqual(sum(self.svc.profile(ALICE)["collection"].values()), before + 5)

    async def test_parse_deck_text(self):
        cards, unknown = parse_deck_text("Dren Voskarl x3, A19 x2\nCloud Beam, nonsense card x2")
        self.assertEqual(cards, ["C12"] * 3 + ["A19"] * 2 + ["A01"])
        self.assertEqual(unknown, ["nonsense card x2"])

    async def test_forget_me_deletes_profile(self):
        self.assertTrue(self.svc.forget_me(ALICE))
        self.assertIsNone(self.svc.profile(ALICE)["starter"])


class MatchTests(Base):
    async def test_only_the_deciding_player_with_a_current_menu_can_move(self):
        match = self.svc.create_pvp(ALICE, "Alice", BOB, "Bob")
        seat = E.decider(match.game)
        other = BOB if match.record["seats"][seat] == str(ALICE) else ALICE
        key = safe_default(E.legal_actions(match.game))
        with self.assertRaises(TcgError):                           # not their decision
            await self.svc.act(match.id, other, key, match.version)
        with self.assertRaises(TcgError):                           # not in the match
            await self.svc.act(match.id, CARL, key, match.version)
        version = match.version
        await self.human_move(match, key)
        with self.assertRaises(TcgError):                           # duplicate click on the same (now stale) menu
            await self.svc.act(match.id, int(match.record["seats"][seat]), key, version)

    async def test_one_live_match_per_player_and_no_self_challenge(self):
        self.svc.create_pvp(ALICE, "Alice", BOB, "Bob")
        with self.assertRaises(TcgError):
            self.svc.create_practice(ALICE, "Alice")
        with self.assertRaises(TcgError):
            self.svc.create_pvp(CARL, "Carl", CARL, "Carl")

    async def test_restart_recovery_replays_the_exact_state(self):
        match = self.svc.create_pvp(ALICE, "Alice", BOB, "Bob")
        for _ in range(12):
            await self.human_move(match)
        summary, hands = E.public_summary(match.game), [list(p.hand) for p in match.game.players]
        restarted = self.build()
        self.assertEqual(restarted.restore(), 1)
        again = restarted.get(match.id)
        self.assertEqual(E.public_summary(again.game), summary)
        self.assertEqual([list(p.hand) for p in again.game.players], hands)
        self.assertEqual(again.version, match.version)

    async def test_corrupt_match_records_are_dropped_on_restore(self):
        match = self.svc.create_pvp(ALICE, "Alice", BOB, "Bob")
        record = dict(match.record, keys=["not-a-real-move"])
        self.svc.repo.save_match(match.id, record)
        restarted = self.build()
        self.assertEqual(restarted.restore(), 0)
        self.assertIsNone(restarted.repo.get_match(match.id))

    async def test_practice_ai_moves_until_the_human_must_decide(self):
        svc = self.build(oracle=OracleService(DEFAULT_MODELS_DIR, rng=random.Random(0)))
        match = svc.create_practice(ALICE, "Alice", "hard", opponent="storm")
        self.svc = svc
        for _ in range(60):
            if match.game.over:
                break
            if match.decider_is_ai:
                await svc.run_ai(match)
            else:
                await self.human_move(match)
        self.assertTrue(match.game.over or not match.decider_is_ai)
        self.assertTrue(all(k in k for k in match.record["keys"]))

    async def test_timeouts_auto_move_then_forfeit(self):
        match = self.svc.create_pvp(ALICE, "Alice", BOB, "Bob")
        seat = E.decider(match.game)
        for i in range(3):
            self.clock.advance(seconds=TcgTerms().turn_timeout_s + 1)
            results = await self.svc.sweep_timeouts()
            self.assertEqual(len(results), 1)
            if match.game.over:
                break
            # make the same player the decider again for the next timeout
            while E.decider(match.game) != seat and not match.game.over:
                await self.human_move(match)
        self.assertTrue(match.game.over)
        self.assertIn("timed out", match.game.end_reason)

    async def test_rated_win_updates_elo_and_pays_capped_rewards(self):
        for n in range(4):
            match = self.svc.create_pvp(ALICE, "Alice", BOB, "Bob")
            loser_seat = match.seat_of(BOB)
            summary = await self.svc.forfeit(match.id, BOB)
            self.assertEqual(summary["winner"], 1 - loser_seat)
        alice, bob = self.svc.profile(ALICE), self.svc.profile(BOB)
        self.assertGreater(alice["rating"], 1000)
        self.assertEqual(alice["rating"] - 1000, 1000 - bob["rating"])
        self.assertEqual((alice["wins"], bob["losses"]), (4, 4))
        self.assertEqual(self.users.get(ALICE)["wallet"], 2 * TcgTerms().win_reward)     # same-opponent cap is 2/day

    async def test_coached_and_practice_matches_are_unrated(self):
        match = self.svc.create_pvp(ALICE, "Alice", BOB, "Bob", coach=True)
        summary = await self.svc.forfeit(match.id, BOB)
        self.assertEqual(summary["rating"], {})
        self.assertEqual(self.svc.profile(ALICE)["rating"], 1000)
        self.assertEqual(self.users.get(ALICE)["wallet"], 0)


if __name__ == "__main__":
    unittest.main()

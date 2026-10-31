"""Asgrelia: Cloudfall rules engine: one test per mechanic in the Codex rulebook, plus replay/fuzz integration tests."""
import random
import unittest

from rudhabot.tcg import engine as E
from rudhabot.tcg.decks import ARCHETYPES, validate_deck
from rudhabot.tcg.state import Unit

VANGUARD = list(ARCHETYPES["vanguard"]["cards"])
STORM = list(ARCHETYPES["storm"]["cards"])


def fresh(first=0, seed=3, decks=(VANGUARD, STORM)):
    g = E.new_game([list(decks[0]), list(decks[1])], ["A", "B"], seed, first=first)
    E.apply(g, "p:mulligan:keep")
    E.apply(g, "p:mulligan:keep")
    settle(g)
    return g


def settle(g):
    """Resolve any queued prompts with their first (or skip) option."""
    while g.pending and not g.over:
        acts = E.legal_actions(g)
        skip = next((a for a in acts if a.key.endswith(":skip") or a.key.endswith(":pass") or a.key.endswith(":keep")), None)
        E.apply(g, (skip or acts[0]).key)


def put(g, seat, cid, slot, old=True, **attrs) -> Unit:
    u = Unit(g.next_uid, cid, seat, g.turn - 2 if old else g.turn)
    g.next_uid += 1
    for k, v in attrs.items():
        setattr(u, k, v)
    g.players[seat].field[slot] = u
    return u


def keys(g):
    return [a.key for a in E.legal_actions(g)]


def attack_with(g, *units, defender_blocks=None):
    """From the active player's Main phase: go to Clash, declare `units`, apply `defender_blocks` {attacker: blocker}."""
    E.apply(g, "clash"); settle(g)
    for u in units:
        E.apply(g, f"atk:{u.uid}"); settle(g)
    if "done" in keys(g):
        E.apply(g, "done")
    defender_blocks = defender_blocks or {}
    while g.phase == "intercept" and not g.pending:
        attacker = g.block_queue[0]
        blocker = defender_blocks.get(attacker)
        E.apply(g, f"blk:{blocker.uid}" if blocker else "noblock")
    settle(g)


class DeckRuleTests(unittest.TestCase):
    def test_all_six_archetypes_are_legal_40_card_decks(self):
        for key, arche in ARCHETYPES.items():
            self.assertEqual(validate_deck(arche["cards"]), [], key)

    def test_validation_enforces_size_characters_and_rarity(self):
        self.assertTrue(any("exactly 40" in p for p in validate_deck(VANGUARD + ["A01"])))
        two_grade5 = [c for c in VANGUARD if c != "A22"] + ["C01"] + ["A22"]
        two_grade5.remove("A22")
        self.assertTrue(any("Grade 5" in p for p in validate_deck(two_grade5)))
        too_many = [c for c in VANGUARD if c != "A03"] + ["C12"] * 2
        self.assertTrue(any("Dren Voskarl: at most 3" in p for p in validate_deck(too_many)))
        few_chars = [c for c in VANGUARD if not c.startswith("C")][:28] + ["C12"] * 3 + ["C14"] * 3 + ["C10"] * 3 + ["C13"] * 2 + ["E01"]
        self.assertTrue(any("at least 12 Characters" in p for p in validate_deck(few_chars)))
        self.assertTrue(validate_deck(["ZZZ"]))


class SetupAndVaporTests(unittest.TestCase):
    def test_setup_resolve_hands_first_player_skips_draw_second_draws_two(self):
        g = fresh(first=0)
        self.assertEqual([p.resolve for p in g.players], [30, 30])
        self.assertEqual(len(g.players[0].hand), 5)                 # first player skipped the first draw
        self.assertEqual(g.players[0].vapor, 2)                     # capacity 1 -> 2 at the first Dawn
        E.apply(g, "clash"); settle(g)
        if "done" in keys(g):
            E.apply(g, "done")
        settle(g)
        self.assertEqual(g.active, 1)
        self.assertEqual(len(g.players[1].hand), 7)                 # second player draws two at their first Dawn
        self.assertEqual(g.players[1].vapor, 2)

    def test_capacity_rises_each_dawn_and_caps_at_eight(self):
        g = fresh()
        g.players[1].base_capacity = 8
        E._start_turn(g, 1); settle(g)
        self.assertEqual(E.capacity(g, 1), 8)
        g.players[1].landmark = "M07"
        self.assertEqual(E.capacity(g, 1), 9)                       # effects can push past 8 (to 10 max)


class CombatTests(unittest.TestCase):
    def test_unintercepted_attacker_hits_resolve(self):
        g = fresh()
        dren = put(g, 0, "C12", 0)
        attack_with(g, dren)
        self.assertEqual(g.players[1].resolve, 30 - 2)
        self.assertFalse(dren.ready)                                # attackers become Spent

    def test_new_character_cannot_attack_unless_swift(self):
        g = fresh()
        fresh_rhea = put(g, 0, "C14", 0, old=False)
        fresh_dren = put(g, 0, "C12", 1, old=False)
        E.apply(g, "clash"); settle(g)
        self.assertNotIn(f"atk:{fresh_rhea.uid}", keys(g))
        self.assertIn(f"atk:{fresh_dren.uid}", keys(g))             # Dren has Swift

    def test_first_strike_kills_before_the_return_blow(self):
        g = fresh()
        kesh = put(g, 0, "C32", 0)                                  # 3/3 First Strike
        dren = put(g, 1, "C12", 0)                                  # 2/1
        attack_with(g, kesh, defender_blocks={kesh.uid: dren})
        self.assertIsNone(g.unit(dren.uid))
        self.assertEqual(kesh.damage, 0)

    def test_simultaneous_trade(self):
        g = fresh()
        rhea = put(g, 0, "C14", 0)                                  # 2/2
        dren = put(g, 1, "C12", 0)                                  # 2/1
        attack_with(g, rhea, defender_blocks={rhea.uid: dren})
        self.assertIsNone(g.unit(dren.uid))
        self.assertIsNone(g.unit(rhea.uid))                         # 2 damage on 2 Stamina

    def test_piercing_sends_excess_to_resolve(self):
        g = fresh()
        dren = put(g, 0, "C12", 0, corrupted=True)                  # corrupted: 4/3 and Piercing
        fern = put(g, 1, "C36", 0)                                  # 1/3
        attack_with(g, dren, defender_blocks={dren.uid: fern})
        self.assertEqual(g.players[1].resolve, 29)

    def test_frozen_characters_cannot_attack_or_intercept(self):
        g = fresh()
        dren = put(g, 0, "C12", 0, frozen=True)
        E.apply(g, "clash"); settle(g)
        self.assertNotIn(f"atk:{dren.uid}", keys(g))

    def test_skyborne_cannot_be_intercepted_by_lower_grade(self):
        g = fresh()
        pelle = put(g, 0, "C26", 0)                                 # Skyborne, Grade 1
        dren = put(g, 1, "C12", 0)                                  # Grade 1: may intercept (not lower)
        E.apply(g, "clash"); settle(g)
        E.apply(g, f"atk:{pelle.uid}"); E.apply(g, "done")
        self.assertIn(f"blk:{dren.uid}", keys(g))
        g2 = fresh()
        odalys = put(g2, 0, "C27", 0, boons=["B06"])                # Grade 3 with the Skyborne Boon
        low = put(g2, 1, "C12", 1)
        E.apply(g2, "clash"); settle(g2)
        E.apply(g2, f"atk:{odalys.uid}"); E.apply(g2, "done")
        self.assertNotIn(f"blk:{low.uid}", keys(g2))

    def test_damage_heals_at_dusk_except_on_the_corrupted(self):
        g = fresh()
        a = put(g, 1, "C23", 0, damage=2)
        b = put(g, 1, "C23", 1, damage=2, corrupted=True)
        E.apply(g, "clash"); settle(g)
        if "done" in keys(g):
            E.apply(g, "done")
        settle(g)
        self.assertEqual(a.damage, 0)
        self.assertEqual(b.damage, 2)


class CorruptionAndThreatTests(unittest.TestCase):
    def test_three_taint_corrupts_raises_threat_and_grants_plus_two(self):
        g = fresh()
        u = put(g, 0, "C14", 0)
        base = E.power(g, u), E.stamina(g, u)
        E.place_taint(g, u, 3, 1)
        self.assertTrue(u.corrupted)
        self.assertEqual(u.taint, 0)
        self.assertEqual(g.threat, 1)
        self.assertEqual((E.power(g, u), E.stamina(g, u)), (base[0] + 2, base[1] + 2 + 1))   # +1: Hazard is now in effect
        E.place_taint(g, u, 5, 1)
        self.assertEqual(u.taint, 0)                                # a Corrupted character ignores Taint

    def test_fallen_enters_corrupted_and_raises_threat(self):
        g = fresh()
        g.players[0].hand = ["C21"]
        g.players[0].vapor = 5
        E.apply(g, "c:C21:s0"); settle(g)
        orlan = g.players[0].field[0]
        self.assertTrue(orlan.corrupted)
        self.assertEqual(g.threat, 1)

    def test_purify_ends_corruption_spends_and_lowers_threat(self):
        g = fresh()
        u = put(g, 0, "C14", 0)
        E.place_taint(g, u, 3, 1)
        E.purify(g, u)
        self.assertFalse(u.corrupted)
        self.assertFalse(u.ready)
        self.assertEqual(g.threat, 0)
        E.place_taint(g, u, 1, 1)
        self.assertEqual(u.taint, 0)                                # can't gain Taint until the end of the round

    def test_corruption_spreads_to_neighbours_at_dusk(self):
        g = fresh()
        put(g, 0, "C14", 1, corrupted=True)
        left, right, far = put(g, 0, "C12", 0), put(g, 0, "C10", 2), put(g, 0, "C13", 3)
        E._dusk(g); settle(g)
        self.assertEqual((left.taint, right.taint, far.taint), (1, 1, 0))

    def test_threat_rises_at_round_end_from_round_three_but_not_under_grave(self):
        g = fresh()
        g.round = 2
        E._round_end(g)
        self.assertEqual(g.threat, 0)
        E._round_end(g)                                             # round 3 ends
        self.assertEqual(g.threat, 1)
        g.threat = 2                                                # Grave
        E._round_end(g)
        self.assertEqual(g.threat, 2)

    def test_threat_is_clamped_to_zero_and_eight(self):
        g = fresh()
        E.change_threat(g, -3)
        self.assertEqual(g.threat, 0)
        E.change_threat(g, 20)
        self.assertEqual(g.threat, 8)

    def test_ko_of_a_corrupted_character_lowers_threat(self):
        g = fresh()
        u = put(g, 1, "C12", 0)
        E.place_taint(g, u, 3, 0)
        u.damage = 99
        E.check_kos(g)
        self.assertIsNone(g.unit(u.uid))
        self.assertEqual(g.threat, 0)


class ProtocolSkyTests(unittest.TestCase):
    def test_calamity_blocks_new_landmarks_and_skies(self):
        g = fresh()
        put(g, 0, "C22", 0)
        g.players[0].hand = ["M03", "S3"]
        g.players[0].vapor = 9
        self.assertTrue(any(k.startswith("c:M03") for k in keys(g)))
        g.threat = 6
        self.assertFalse(any(k.startswith("c:M03") or k.startswith("c:S3") for k in keys(g)))

    def test_armageddon_ignores_grade_requirements(self):
        g = fresh()
        put(g, 0, "C12", 0)
        g.players[0].hand = ["A04"]
        g.players[0].vapor = 5
        self.assertFalse(any(k.startswith("c:A04") for k in keys(g)))
        g.threat = 8
        self.assertTrue(any(k.startswith("c:A04") for k in keys(g)))

    def test_befalling_and_fright_bonuses(self):
        g = fresh()
        a, b = put(g, 0, "C12", 0), put(g, 0, "C14", 1)
        g.threat = 3                                                # Fright: +1 per other ally sharing an Affiliation
        self.assertEqual(E.power(g, a), 2 + 1)
        g.threat = 7                                                # Befalling: +2/+0
        self.assertEqual(E.power(g, a), 2 + 2)

    def test_zenith_and_overcast_change_grades(self):
        g = fresh()
        u = put(g, 0, "C27", 0)
        g.sky = "S1"
        self.assertEqual(E.grade(g, u), 4)
        g.sky = "S2"
        self.assertEqual(E.grade(g, u), 2)
        E.place_taint(g, u, 1, 1)
        self.assertEqual(u.taint, 0)                                # Overcast Veil: no Taint


class TargetingAndCardTests(unittest.TestCase):
    def test_grade_requirement_gates_powered_cards(self):
        g = fresh()
        g.players[0].hand = ["A04"]
        g.players[0].vapor = 5
        put(g, 0, "C12", 0)
        self.assertFalse(any(k.startswith("c:A04") for k in keys(g)))
        put(g, 0, "C27", 1)                                         # Grade 3 Odalys
        self.assertTrue(any(k.startswith("c:A04") for k in keys(g)))

    def test_command_cards_need_a_character_that_can_speak(self):
        g = fresh()
        g.players[0].hand = ["A19"]
        g.players[0].vapor = 5
        put(g, 0, "C12", 0, corrupted=True)
        self.assertFalse(any(k.startswith("c:A19") for k in keys(g)))
        put(g, 0, "C14", 1)
        self.assertTrue(any(k.startswith("c:A19") for k in keys(g)))

    def test_guard_forces_enemy_actions_onto_guards(self):
        g = fresh()
        g.players[0].hand = ["A01", "A02"]
        g.players[0].vapor = 9
        hollis, dren = put(g, 1, "C23", 0), put(g, 1, "C12", 1)
        k = keys(g)
        self.assertIn(f"c:A01:u{hollis.uid}", k)
        self.assertNotIn(f"c:A01:u{dren.uid}", k)
        self.assertIn(f"c:A02:u{dren.uid}", k)                      # Curved Beam ignores Guard

    def test_compressed_chain_adds_damage_to_the_second_beam(self):
        g = fresh(decks=(VANGUARD, VANGUARD))
        g.players[0].hand = ["A01", "A01"]
        g.players[0].vapor = 9
        a, b = put(g, 1, "C23", 0), put(g, 1, "C23", 1)             # 1/4 Guards
        E.apply(g, f"c:A01:u{a.uid}"); settle(g)
        E.apply(g, f"c:A01:u{b.uid}"); settle(g)
        self.assertEqual((a.damage, b.damage), (3, 4))

    def test_soaked_takes_two_extra_lightning_damage(self):
        g = fresh()
        u = put(g, 1, "C23", 0, soaked=True)
        E.deal(g, ("u", u.uid), 3, {"kind": "action", "seat": 0, "power": "Lightning"})
        self.assertEqual(u.damage, 5)

    def test_cold_front_spends_soaked_for_three_damage(self):
        g = fresh()
        put(g, 0, "C27", 0)                                         # Grade 3
        g.players[0].hand = ["A05"]
        g.players[0].vapor = 5
        target = put(g, 1, "C23", 0, soaked=True)
        E.apply(g, f"c:A05:u{target.uid}"); settle(g)
        self.assertTrue(target.frozen)
        self.assertFalse(target.soaked)
        self.assertEqual(target.damage, 3)

    def test_mirror_cloud_negates_and_punishes(self):
        g = fresh()
        g.players[0].hand = ["A01"]
        g.players[0].vapor = 5
        target = put(g, 1, "C12", 0)
        g.players[1].hand = ["A14"]
        g.players[1].vapor = 2
        E.apply(g, f"c:A01:u{target.uid}")
        self.assertEqual(E.decider(g), 1)
        E.apply(g, "p:respond:A14")
        settle(g)
        self.assertIsNotNone(g.unit(target.uid))
        self.assertEqual(target.damage, 0)
        self.assertEqual(g.players[0].resolve, 30 - 2)              # half of 3, rounded up

    def test_cloud_shield_reaction_prevents_damage(self):
        g = fresh()
        g.players[0].hand = ["A01"]
        g.players[0].vapor = 5
        target = put(g, 1, "C20", 0)
        g.players[1].hand = ["A13"]
        g.players[1].vapor = 1
        E.apply(g, f"c:A01:u{target.uid}")
        E.apply(g, f"p:respond:A13u{target.uid}")
        settle(g)
        self.assertEqual(target.damage, 0)

    def test_reach_deeper_is_once_per_turn(self):
        g = fresh()
        u = put(g, 0, "C12", 0)
        g.players[0].hand = ["A24", "A24"]
        g.players[0].vapor = 0
        E.apply(g, f"c:A24:u{u.uid}"); settle(g)
        self.assertEqual(g.players[0].vapor, 3)
        self.assertEqual(u.taint, 1)
        self.assertFalse(any(k.startswith("c:A24") for k in keys(g)))

    def test_equipment_slots_and_stat_bonus(self):
        g = fresh()
        u = put(g, 0, "C12", 0)
        g.players[0].hand = ["E01", "E01"]
        g.players[0].vapor = 9
        E.apply(g, f"c:E01:u{u.uid}"); settle(g)
        self.assertEqual(E.power(g, u), 4)
        self.assertFalse(any(k.startswith("c:E01") for k in keys(g)))   # one Weapon per character

    def test_bond_harp_and_lana(self):
        g = fresh()
        harp = put(g, 0, "C01", 0)
        self.assertEqual(E.power(g, harp), 4)
        put(g, 0, "C08", 1)
        self.assertEqual((E.power(g, harp), E.stamina(g, harp)), (5, 7))

    def test_illegal_action_is_rejected_without_changing_state(self):
        g = fresh()
        before = (g.phase, len(g.history), g.players[0].vapor)
        with self.assertRaises(E.IllegalAction):
            E.apply(g, "c:A12:")
        self.assertEqual(before, (g.phase, len(g.history), g.players[0].vapor))


class WinConditionTests(unittest.TestCase):
    def test_resolve_zero_loses_and_simultaneous_is_a_draw(self):
        g = fresh()
        g.players[1].resolve = 0
        E.check_kos(g)
        self.assertEqual(g.winner, 0)
        g2 = fresh()
        g2.players[0].resolve = g2.players[1].resolve = -1
        E.check_kos(g2)
        self.assertEqual(g2.winner, -1)

    def test_drawing_from_an_empty_deck_loses(self):
        g = fresh()
        g.players[1].deck = []
        E.draw(g, 1)
        self.assertEqual(g.winner, 0)

    def test_the_prophecy_wins_at_dusk(self):
        g = fresh()
        g.players[0].landmark = "M09"
        g.threat = 8
        for slot in range(3):
            put(g, 0, "C21", slot, corrupted=True)
        E._step_dusk2(g)
        self.assertEqual(g.winner, 0)
        self.assertIn("Prophecy", g.end_reason)


class IntegrationTests(unittest.TestCase):
    def play_random(self, seed, steps=10_000):
        rng = random.Random(seed)
        keys_ = list(ARCHETYPES)
        a, b = rng.choice(keys_), rng.choice(keys_)
        decks = [list(ARCHETYPES[a]["cards"]), list(ARCHETYPES[b]["cards"])]
        g = E.new_game(decks, ["A", "B"], seed)
        n = 0
        while not g.over and n < steps:
            acts = E.legal_actions(g)
            self.assertTrue(acts, f"no legal actions in phase {g.phase}")
            E.apply(g, rng.choice(acts).key)
            n += 1
        return g, decks

    def test_random_legal_play_always_finishes_without_errors(self):
        for seed in range(40):
            g, _ = self.play_random(seed)
            self.assertTrue(g.over, seed)
            for u in g.all_units():
                self.assertLess(u.damage, E.stamina(g, u))          # nobody is left on the field past lethal
            self.assertTrue(0 <= g.threat <= 8)

    def test_replay_with_recorded_first_player_stays_in_sync_through_shuffles(self):
        g = E.new_game([list(VANGUARD), list(STORM)], ["A", "B"], 77)
        E.apply(g, "p:mulligan:all")                                # mulligan shuffles the deck: uses the match RNG
        E.apply(g, "p:mulligan:all")
        again = E.replay([list(VANGUARD), list(STORM)], ["A", "B"], 77, g.history, first=g.first)
        self.assertEqual([p.hand for p in again.players], [p.hand for p in g.players])
        self.assertEqual([p.deck for p in again.players], [p.deck for p in g.players])

    def test_replay_reproduces_the_exact_match(self):
        g, decks = self.play_random(123, steps=150)
        again = E.replay(decks, ["A", "B"], 123, g.history)
        self.assertEqual(E.public_summary(again), E.public_summary(g))
        self.assertEqual([p.hand for p in again.players], [p.hand for p in g.players])
        self.assertEqual(again.log, g.log)


if __name__ == "__main__":
    unittest.main()

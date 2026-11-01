"""Mutable match state for Asgrelia: Cloudfall. Plain data with a fast `clone()` (used heavily by the AI search).

Everything a match needs lives here: the engine (`engine.py`) is a set of pure functions over this state, and every
decision point is a data-only prompt, so a match can be replayed exactly from (seed, decks, action keys).
"""
from __future__ import annotations

import random
from typing import Optional

from rudhabot.tcg.cards import CARD_BY_ID


class Unit:
    """A character on the field."""
    __slots__ = ("uid", "cid", "owner", "damage", "taint", "corrupted", "ready", "frozen", "soaked", "entered_turn",
                 "weapon", "gear", "boons", "afflictions", "tmp_power", "tmp_stamina", "tmp_swift", "disrupt",
                 "perm_power", "perm_stamina", "no_taint_round", "shield", "aegis_used", "plating_used", "marked_used",
                 "fog", "sworn", "attacked")

    def __init__(self, uid: int, cid: str, owner: int, turn: int):
        self.uid, self.cid, self.owner = uid, cid, owner
        self.damage = self.taint = 0
        self.corrupted = CARD_BY_ID[cid].fallen
        self.ready, self.frozen, self.soaked = True, False, False
        self.entered_turn = turn
        self.weapon: Optional[str] = None
        self.gear: Optional[str] = None
        self.boons: list[str] = []
        self.afflictions: list[str] = []
        self.tmp_power = self.tmp_stamina = 0
        self.tmp_swift = False
        self.disrupt = 0
        self.perm_power = self.perm_stamina = 0
        self.no_taint_round = False
        self.shield = 0
        self.aegis_used = self.plating_used = self.marked_used = False
        self.fog = False
        self.sworn: Optional[int] = None
        self.attacked = False

    @property
    def card(self):
        return CARD_BY_ID[self.cid]

    def equipment(self) -> list[str]:
        return [e for e in (self.weapon, self.gear) if e]

    def clone(self) -> "Unit":
        u = Unit.__new__(Unit)
        _copy_unit_fields(self, u)
        u.boons, u.afflictions = list(self.boons), list(self.afflictions)
        return u


def _make_field_copier(slots: tuple[str, ...]):
    """A function copying every slot with plain attribute assignments: several times faster than a getattr/setattr
    loop, and clone() is the hottest call in AI search."""
    body = "\n".join(f"    dst.{name} = src.{name}" for name in slots)
    namespace: dict = {}
    exec(f"def copy_fields(src, dst):\n{body}\n", namespace)
    return namespace["copy_fields"]


_copy_unit_fields = _make_field_copier(Unit.__slots__)


class Player:
    __slots__ = ("seat", "name", "deck", "hand", "rainfall", "field", "landmark", "resolve", "vapor", "base_capacity",
                 "turn_flags", "round_flags", "dawns", "actions_this_turn", "shield")

    def __init__(self, seat: int, name: str, deck: list[str]):
        self.seat, self.name = seat, name
        self.deck = list(deck)
        self.hand: list[str] = []
        self.rainfall: list[str] = []
        self.field: list[Optional[Unit]] = [None, None, None, None]
        self.landmark: Optional[str] = None
        self.resolve = 30
        self.vapor = 0
        self.base_capacity = 1
        self.turn_flags: set[str] = set()       # once-per-turn uses, powers played this turn, ...
        self.round_flags: set[str] = set()      # once-per-round uses
        self.dawns = 0
        self.actions_this_turn = 0
        self.shield = 0                          # Cloud Shield prevention on the player, this turn

    def units(self) -> list[Unit]:
        return [u for u in self.field if u is not None]

    def clone(self) -> "Player":
        p = Player.__new__(Player)
        p.seat, p.name = self.seat, self.name
        p.deck, p.hand, p.rainfall = list(self.deck), list(self.hand), list(self.rainfall)
        p.field = [u.clone() if u else None for u in self.field]
        p.landmark, p.resolve, p.vapor, p.base_capacity = self.landmark, self.resolve, self.vapor, self.base_capacity
        p.turn_flags, p.round_flags = set(self.turn_flags), set(self.round_flags)
        p.dawns, p.actions_this_turn, p.shield = self.dawns, self.actions_this_turn, self.shield
        return p


class Game:
    """Phases: mulligan, main, declare, intercept, over. Prompts in `pending` always take priority over the phase."""

    def __init__(self, decks: list[list[str]], names: list[str], seed: int, beginner_level: int = 3):
        self.seed = seed
        self.rng = random.Random(seed)
        self.players = [Player(0, names[0], decks[0]), Player(1, names[1], decks[1])]
        self.level = beginner_level
        self.active = 0
        self.first = 0
        self.round = 1
        self.turn = 0
        self.phase = "mulligan"
        self.threat = 0
        self.sky: Optional[str] = None
        self.sky_owner: Optional[int] = None
        self.mandate: Optional[tuple[int, int]] = None      # (seat of Vortan's controller, offset)
        self.pending: list[dict] = []                        # decision prompts, first one is current
        self.resume: list[str] = []                          # flow continuations to run when `pending` empties
        self.stack: Optional[dict] = None                    # an Action waiting for responses
        self.attackers: list[int] = []
        self.blocks: dict[int, int] = {}
        self.block_queue: list[int] = []
        self.winner: Optional[int] = None                    # 0 / 1, or -1 for a draw
        self.end_reason = ""
        self.next_uid = 1
        self.log: list[str] = []
        self.history: list[str] = []                         # action keys, for replay / persistence
        self.protocol_seen = 0

    def other(self, seat: int) -> int:
        return 1 - seat

    def unit(self, uid: int) -> Optional[Unit]:
        for p in self.players:
            for u in p.field:
                if u is not None and u.uid == uid:
                    return u
        return None

    def all_units(self) -> list[Unit]:
        return [u for p in self.players for u in p.field if u is not None]

    def slot_of(self, unit: Unit) -> int:
        return self.players[unit.owner].field.index(unit)

    @property
    def over(self) -> bool:
        return self.winner is not None

    def say(self, text: str) -> None:
        self.log.append(text)
        if len(self.log) > 400:
            del self.log[:100]

    def clone(self) -> "Game":
        g = Game.__new__(Game)
        g.seed, g.level = self.seed, self.level
        g.rng = random.Random.__new__(random.Random)      # no seeding from the OS: setstate fills in everything
        g.rng.setstate(self.rng.getstate())
        g.players = [p.clone() for p in self.players]
        g.active, g.first, g.round, g.turn, g.phase = self.active, self.first, self.round, self.turn, self.phase
        g.threat, g.sky, g.sky_owner, g.mandate = self.threat, self.sky, self.sky_owner, self.mandate
        g.pending = [dict(p) for p in self.pending]
        g.resume = list(self.resume)
        g.stack = None if self.stack is None else {**self.stack, "targets": list(self.stack["targets"])}
        g.attackers, g.blocks, g.block_queue = list(self.attackers), dict(self.blocks), list(self.block_queue)
        g.winner, g.end_reason, g.next_uid = self.winner, self.end_reason, self.next_uid
        g.log = []                      # clones are for search; they do not need the narrative
        g.history = []
        g.protocol_seen = self.protocol_seen
        return g

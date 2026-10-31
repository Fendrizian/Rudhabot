"""State encoding for the Cloudfall Oracle.

`encode(game, seat)` returns what *that player could legitimately know*: the public board, both players' public
counters, and their own hand. It never reads the opponent's hand or either deck's order (`test_ml` checks this by
scrambling hidden zones and asserting identical features).

Output: (dense float32[DENSE_DIM], bags float32[3, N_CARDS]) where the bags are card counts for
(own hand, own board incl. attachments and Landmark, opponent board). The model embeds the bags with a shared learned
card-embedding matrix, which is what gives Deck Doctor its notion of card similarity.
"""
from __future__ import annotations

import hashlib

import numpy as np

from rudhabot.tcg import engine as E
from rudhabot.tcg.cards import CARD_IDS, CARD_INDEX
from rudhabot.tcg.state import Game

N_CARDS = len(CARD_IDS)
LANDMARKS = ("M01", "M02", "M03", "M04", "M05", "M06", "M07", "M08", "M09")
SKIES = ("S1", "S2", "S3", "S4")
PHASES = ("main", "declare", "intercept", "clash")
SLOT_FEATURES = 20
GLOBAL_DIM = 9 + 9 + 5 + 1 + 1 + 5 + 1
PLAYER_DIM = 10 + 10
PREVIEW_DIM = 4
DENSE_DIM = GLOBAL_DIM + 2 * PLAYER_DIM + 8 * SLOT_FEATURES + PREVIEW_DIM

FEATURE_NAMES_VERSION = "cloudfall-features-v1"


def schema_hash() -> str:
    """Changes whenever the encoding layout changes, so a stale artifact is refused instead of silently misread."""
    spec = f"{FEATURE_NAMES_VERSION}|{DENSE_DIM}|{N_CARDS}|{','.join(CARD_IDS)}|{SLOT_FEATURES}"
    return hashlib.sha256(spec.encode()).hexdigest()[:16]


def _slot(g: Game, u, out: np.ndarray, at: int) -> None:
    if u is None:
        return
    st = E.stamina(g, u)
    out[at:at + SLOT_FEATURES] = (
        1.0, E.power(g, u) / 10, st / 10, (st - u.damage) / 10, E.grade(g, u) / 5, u.taint / 3, u.corrupted, u.ready,
        u.frozen, u.soaked, len(u.equipment()) / 2, len(u.boons) / 2, len(u.afflictions) / 2,
        E.has_kw(g, u, "Guard"), E.can_attack(g, u) if g.active == u.owner else 0.0, E.has_kw(g, u, "First Strike"),
        E.has_kw(g, u, "Skyborne"), E.has_kw(g, u, "Piercing"), u.uid in g.attackers, u.uid in g.blocks.values(),
    )


def _preview(g: Game, me: int) -> tuple[float, float, float, float]:
    """Projected Clash outcome for the attackers/interceptors declared so far (from `me`'s point of view)."""
    if not g.attackers:
        return 0.0, 0.0, 0.0, 0.0
    to_resolve = lost_att = lost_def = 0
    for aid in g.attackers:
        a = g.unit(aid)
        if a is None:
            continue
        iid = g.blocks.get(aid)
        i = g.unit(iid) if iid is not None else None
        if i is None:
            if g.phase != "intercept" or aid not in g.block_queue:
                to_resolve += E.power(g, a, role="attack")
            continue
        if E.power(g, a, i, "attack") >= E.remaining(g, i):
            lost_def += 1
        if E.power(g, i, a, "intercept") >= E.remaining(g, a):
            lost_att += 1
    sign = 1.0 if g.active == me else -1.0
    return sign * to_resolve / 30, sign * (lost_def - lost_att) / 4, lost_att / 4, lost_def / 4


def encode(g: Game, me: int) -> tuple[np.ndarray, np.ndarray]:
    dense = np.zeros(DENSE_DIM, dtype=np.float32)
    bags = np.zeros((3, N_CARDS), dtype=np.float32)
    opp = g.other(me)
    i = 0
    dense[i + g.threat] = 1; i += 9
    dense[i + E.protocol(g)] = 1; i += 9
    dense[i + (SKIES.index(g.sky) + 1 if g.sky else 0)] = 1; i += 5
    dense[i] = min(g.round, 20) / 20; i += 1
    dense[i] = g.active == me; i += 1
    dense[i + (PHASES.index(g.phase) if g.phase in PHASES else 4)] = 1; i += 5
    dense[i] = E.decider(g) == me; i += 1
    for seat in (me, opp):
        p = g.players[seat]
        units = p.units()
        dense[i:i + 10] = (p.resolve / 30, p.vapor / 10, E.capacity(g, seat) / 10, len(p.hand) / 8, len(p.deck) / 40,
                           len(p.rainfall) / 40, len(units) / 4, sum(u.corrupted for u in units) / 4,
                           sum(E.power(g, u) for u in units) / 20,
                           sum(E.remaining(g, u) for u in units) / 30)
        i += 10
        dense[i + (LANDMARKS.index(p.landmark) + 1 if p.landmark else 0)] = 1
        i += 10
    for seat in (me, opp):
        for u in g.players[seat].field:
            _slot(g, u, dense, i)
            i += SLOT_FEATURES
    dense[i:i + PREVIEW_DIM] = _preview(g, me)
    for cid in g.players[me].hand:
        bags[0, CARD_INDEX[cid]] += 1
    for row, seat in ((1, me), (2, opp)):
        p = g.players[seat]
        if p.landmark:
            bags[row, CARD_INDEX[p.landmark]] += 1
        for u in p.units():
            for cid in (u.cid, *u.equipment(), *u.boons, *u.afflictions):
                bags[row, CARD_INDEX[cid]] += 1
    if g.sky:
        bags[1 if g.sky_owner == me else 2, CARD_INDEX[g.sky]] += 1
    return dense, bags


def encode_many(games: list[Game], me: int) -> tuple[np.ndarray, np.ndarray]:
    pairs = [encode(g, me) for g in games]
    return np.stack([p[0] for p in pairs]), np.stack([p[1] for p in pairs])

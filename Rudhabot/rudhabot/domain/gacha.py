"""Skyfall Wishes: the Asgrelia: Cloudfall gacha. Pure rules; randomness and time are injected.

Rarity       ★5 Pure White (Grade 5 characters, Unique cards) · ★4 White (Grade 4 characters, other cards costing 5+)
             ★3 Light Gray (Grade 3 characters, other cards costing 3-4) · ★2 Gray (everything else)
Rates        ★5 1.6% · ★4 9.0% · ★3 28.0% · ★2 61.4% (published in /tcg wish banner)
Pity         ★5: from wish 56 the ★5 chance rises 5 points per wish; wish 75 is a guaranteed ★5.
             ★4: every 10th wish without a ★4 or better is a guaranteed ★4.
Featured     Each week's banner features one ★5 and three ★4. A ★5 is the featured one 50% of the time; if not, the
             next ★5 is guaranteed featured ("the Prophecy holds"). A ★4 is one of the featured three 50% of the time.
Cloudshine   4% of all wishes are a foil print (cosmetic only).
Vapor Dust   Copies beyond the most you can put in a deck turn into Dust, which crafts any card you choose.
Convergence  Every wish anyone makes fills the server's weekly Convergence meter; everyone who wished that week can
             claim the milestone rewards.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date

from rudhabot.tcg.cards import CARDS, CHARACTER, Card
from rudhabot.tcg.decks import copy_limit

WISH_COST = 160                     # Cloud Shards per wish
RATES = {5: 0.016, 4: 0.09, 3: 0.28, 2: 0.614}
SOFT_PITY_START = 56
SOFT_PITY_STEP = 0.05          # reaches 96.6% at wish 74; wish 75 is guaranteed
HARD_PITY = 75
FOUR_STAR_PITY = 10
FOIL_RATE = 0.04
DUST_FROM_EXTRA = {2: 5, 3: 20, 4: 80, 5: 300}
CRAFT_COST = {2: 30, 3: 100, 4: 400, 5: 1600}
DAILY_SHARDS = 80
CONVERGENCE_MILESTONES = ((250, 160), (750, 320), (1500, 640))      # (server wishes this week, shards each)


def rarity(card: Card) -> int:
    if card.unique or (card.kind == CHARACTER and card.grade == 5):
        return 5
    if card.kind == CHARACTER:
        return {4: 4, 3: 3}.get(card.grade, 2)
    return 4 if card.cost >= 5 else 3 if card.cost >= 3 else 2


POOL: dict[int, list[str]] = {r: [c.id for c in CARDS if rarity(c) == r] for r in (2, 3, 4, 5)}
STARS = {5: "★★★★★", 4: "★★★★", 3: "★★★", 2: "★★"}


@dataclass(frozen=True)
class Banner:
    key: str
    name: str
    featured5: str | None
    featured4: tuple[str, ...]


def iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def weekly_banner(day: date) -> Banner:
    """A deterministic weekly rotation through every ★5, each with three ★4 picked by the same week number."""
    year, week, _ = day.isocalendar()
    index = year * 53 + week
    five = POOL[5][index % len(POOL[5])]
    fours = sorted(POOL[4])
    r = random.Random(index)
    picks = tuple(r.sample(fours, 3))
    from rudhabot.tcg.cards import CARD_BY_ID
    return Banner(f"featured-{iso_week(day)}", f"Skyfall: {CARD_BY_ID[five].name}", five, picks)


STANDARD = Banner("standard", "Cloudfall Standard", None, ())


def five_star_chance(pity5: int) -> float:
    """Chance of a ★5 on the next wish, given `pity5` wishes since the last ★5."""
    n = pity5 + 1
    if n >= HARD_PITY:
        return 1.0
    if n >= SOFT_PITY_START:
        return min(1.0, RATES[5] + SOFT_PITY_STEP * (n - SOFT_PITY_START + 1))
    return RATES[5]


@dataclass
class PityState:
    pity5: int = 0
    pity4: int = 0
    guaranteed: bool = False         # next ★5 on a featured banner is the featured card


@dataclass(frozen=True)
class Pull:
    card: str
    stars: int
    foil: bool
    featured: bool


def wish(banner: Banner, state: PityState, rng: random.Random) -> Pull:
    """One wish. Mutates `state` (pity counters and the 50/50 guarantee)."""
    roll = rng.random()
    if roll < five_star_chance(state.pity5):
        stars = 5
    elif state.pity4 + 1 >= FOUR_STAR_PITY or roll < five_star_chance(state.pity5) + RATES[4]:
        stars = 4
    elif roll < five_star_chance(state.pity5) + RATES[4] + RATES[3]:
        stars = 3
    else:
        stars = 2
    featured = False
    if stars == 5:
        state.pity5, state.pity4 = 0, 0
        if banner.featured5 and (state.guaranteed or rng.random() < 0.5):
            card, featured, state.guaranteed = banner.featured5, True, False
        else:
            card = rng.choice([c for c in POOL[5] if c != banner.featured5] or POOL[5])
            state.guaranteed = banner.featured5 is not None
    else:
        state.pity5 += 1
        if stars == 4:
            state.pity4 = 0
            if banner.featured4 and rng.random() < 0.5:
                card, featured = rng.choice(banner.featured4), True
            else:
                card = rng.choice(POOL[4])
        else:
            state.pity4 += 1
            card = rng.choice(POOL[stars])
    return Pull(card, stars, rng.random() < FOIL_RATE, featured)


def dust_for_extra(card_id: str, owned_after: int) -> int:
    """Dust produced by the newest copy when it exceeds the deck limit for that card (otherwise 0)."""
    from rudhabot.tcg.cards import CARD_BY_ID
    return DUST_FROM_EXTRA[rarity(CARD_BY_ID[card_id])] if owned_after > copy_limit(card_id) else 0

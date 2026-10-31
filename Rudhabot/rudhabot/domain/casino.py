"""Baccarat (punto banco) and craps. Pure rules: randomness is injected, payouts are returned as net results.

Baccarat: 8-deck shoe, standard third-card rules. Player pays 1:1, Banker 1:1 minus 5% commission, Tie 8:1;
Player/Banker bets push on a tie. House edge ≈ 1.24% (Player), 1.06% (Banker), 14.4% (Tie).

Craps: Pass Line (come-out 7/11 win, 2/3/12 lose, otherwise roll the point again before a 7), Don't Pass (mirror,
12 is a push on the come-out) and the one-roll Field (2 pays 2:1, 12 pays 3:1, 3/4/9/10/11 pay 1:1, 5-8 lose).
House edge ≈ 1.41% (Pass), 1.36% (Don't Pass), 2.78% (Field).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUITS = ("♥", "♦", "♣", "♠")


# ================================================================ baccarat
def baccarat_value(card: str) -> int:
    rank = card[:-1]
    if rank == "A":
        return 1
    if rank in ("10", "J", "Q", "K"):
        return 0
    return int(rank)


def hand_total(cards: list[str]) -> int:
    return sum(baccarat_value(c) for c in cards) % 10


def banker_draws(banker: int, player_third: int | None) -> bool:
    """The punto banco tableau for the banker's third card."""
    if player_third is None:
        return banker <= 5
    if banker <= 2:
        return True
    if banker == 3:
        return player_third != 8
    if banker == 4:
        return 2 <= player_third <= 7
    if banker == 5:
        return 4 <= player_third <= 7
    if banker == 6:
        return player_third in (6, 7)
    return False


@dataclass
class BaccaratRound:
    player: list[str]
    banker: list[str]
    winner: str                # "player" | "banker" | "tie"
    natural: bool

    @property
    def player_total(self) -> int:
        return hand_total(self.player)

    @property
    def banker_total(self) -> int:
        return hand_total(self.banker)


def deal_baccarat(rng: random.Random, decks: int = 8) -> BaccaratRound:
    shoe = [r + s for r in RANKS for s in SUITS] * decks
    rng.shuffle(shoe)
    player, banker = [shoe.pop(), shoe.pop()], [shoe.pop(), shoe.pop()]
    natural = hand_total(player) >= 8 or hand_total(banker) >= 8
    if not natural:
        player_third = None
        if hand_total(player) <= 5:
            player.append(shoe.pop())
            player_third = baccarat_value(player[-1])
        if banker_draws(hand_total(banker), player_third):
            banker.append(shoe.pop())
    p, b = hand_total(player), hand_total(banker)
    winner = "tie" if p == b else ("player" if p > b else "banker")
    return BaccaratRound(player, banker, winner, natural)


def baccarat_payout(bet_on: str, stake: int, round_: BaccaratRound) -> int:
    """Net result for the player (positive = won that much, negative = lost the stake, 0 = push)."""
    if round_.winner == "tie":
        return stake * 8 if bet_on == "tie" else 0
    if bet_on != round_.winner:
        return -stake
    if bet_on == "banker":
        return math.floor(stake * 0.95)
    return stake


# ================================================================ craps
@dataclass
class CrapsRound:
    bet: str                           # "pass" | "dontpass" | "field"
    rolls: list[tuple[int, int]] = field(default_factory=list)
    point: int | None = None
    outcome: str = ""                  # "win" | "lose" | "push"
    multiplier: int = 1                # winnings multiple of the stake when won

    @property
    def last_total(self) -> int:
        return sum(self.rolls[-1])


def _roll(rng: random.Random) -> tuple[int, int]:
    return rng.randint(1, 6), rng.randint(1, 6)


def play_craps(bet: str, rng: random.Random, max_rolls: int = 500) -> CrapsRound:
    r = CrapsRound(bet)
    first = _roll(rng)
    r.rolls.append(first)
    total = sum(first)
    if bet == "field":
        if total in (5, 6, 7, 8):
            r.outcome = "lose"
        else:
            r.outcome, r.multiplier = "win", {2: 2, 12: 3}.get(total, 1)
        return r
    if total in (7, 11):
        r.outcome = "win" if bet == "pass" else "lose"
        return r
    if total in (2, 3):
        r.outcome = "lose" if bet == "pass" else "win"
        return r
    if total == 12:
        r.outcome = "lose" if bet == "pass" else "push"
        return r
    r.point = total
    for _ in range(max_rolls):
        dice = _roll(rng)
        r.rolls.append(dice)
        t = sum(dice)
        if t == r.point:
            r.outcome = "win" if bet == "pass" else "lose"
            return r
        if t == 7:
            r.outcome = "lose" if bet == "pass" else "win"
            return r
    r.outcome = "push"                  # unreachable in practice; never leave a bet unresolved
    return r


def craps_payout(stake: int, r: CrapsRound) -> int:
    if r.outcome == "win":
        return stake * r.multiplier
    if r.outcome == "lose":
        return -stake
    return 0

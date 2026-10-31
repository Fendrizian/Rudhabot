"""Cloudfall application service: collections, decks, packs, and the server-authoritative match lifecycle.

Match integrity
  * Every move goes through `engine.apply`, which re-checks legality against the current state.
  * A per-match asyncio.Lock serialises moves; each move carries the state `version` (number of moves applied) the
    player saw, so a duplicate click, a replayed component or an out-of-date menu is rejected instead of applied twice.
  * After every move the match's replay recipe is persisted; `restore()` rebuilds every live match after a restart.
  * Idle players are auto-moved with the safest option (pass / skip / don't attack) and forfeit after repeated timeouts.
Rewards and rating are decided here by fixed rules, never by the Oracle or the LLM.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from rudhabot.clock import Clock
from rudhabot.persistence.tcg import TcgRepository
from rudhabot.persistence.users import UserRepository
from rudhabot.tcg import engine as E
from rudhabot.tcg.cards import CARD_BY_ID, CARDS, CHARACTER, find_card
from rudhabot.tcg.decks import ARCHETYPES, validate_deck
from rudhabot.tcg.state import Game

log = logging.getLogger("rudhabot.tcg")
AI_SEAT_PREFIX = "ai:"
SAFE_PREFIXES = ("p:respond:pass", "p:mulligan:keep", "p:look_bottom:m0", "p:mandate:m0", "p:threat:t0",
                 "p:burnout:no", "p:overwork:no", "p:swap:s-", "noblock", "done", "clash")


@dataclass(frozen=True)
class TcgTerms:
    pack_price: int = 250
    pack_size: int = 5
    win_reward: int = 150
    daily_reward_wins: int = 3
    same_opponent_rewards: int = 2
    turn_timeout_s: int = 240
    max_timeouts: int = 3
    elo_k: int = 24


class TcgError(Exception):
    """A user-facing refusal (message is shown as-is)."""


@dataclass
class Match:
    record: dict
    game: Game
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def id(self) -> str:
        return self.record["id"]

    @property
    def version(self) -> int:
        return len(self.record["keys"])

    def seat_of(self, user_id) -> Optional[int]:
        for seat, owner in enumerate(self.record["seats"]):
            if owner == str(user_id):
                return seat
        return None

    def is_ai(self, seat: int) -> bool:
        return str(self.record["seats"][seat]).startswith(AI_SEAT_PREFIX)

    @property
    def decider_is_ai(self) -> bool:
        seat = E.decider(self.game)
        return seat is not None and self.is_ai(seat)


@dataclass
class MoveResult:
    match: Match
    new_log: list[str]
    finished: Optional[dict] = None


def parse_deck_text(text: str) -> tuple[list[str], list[str]]:
    """'Dren Voskarl x3, A19 x2, Cloud Beam' -> (card ids, unrecognised entries)."""
    cards, unknown = [], []
    for raw in re.split(r"[,\n;]+", text):
        entry = raw.strip()
        if not entry:
            continue
        m = re.match(r"^(.*?)\s*(?:[x×*]\s*(\d+)|(\d+)\s*[x×*])\s*$", entry, re.IGNORECASE)
        count = 1
        if m and (m.group(2) or m.group(3)):
            entry, count = m.group(1).strip(), int(m.group(2) or m.group(3))
        card = find_card(entry)
        if card is None or card.kind == "Protocol":
            unknown.append(raw.strip())
        else:
            cards.extend([card.id] * max(0, min(count, 4)))
    return cards, unknown


def deck_text(cards: list[str]) -> str:
    counts = Counter(cards)
    return ", ".join(f"{CARD_BY_ID[c].name} x{n}" for c, n in sorted(counts.items()))


class TcgService:
    def __init__(self, repo: TcgRepository, users: UserRepository, clock: Clock, rng: random.Random,
                 terms: TcgTerms = TcgTerms(), oracle=None):
        self.repo, self.users, self.clock, self.rng, self.terms = repo, users, clock, rng, terms
        self.oracle = oracle
        self.matches: dict[str, Match] = {}

    # ================================================================== collection & decks
    def profile(self, user_id) -> dict:
        return self.repo.profile(user_id)

    def claim_starter(self, user_id, archetype: str) -> dict:
        if archetype not in ARCHETYPES:
            raise TcgError("Unknown starter deck.")
        profile = self.repo.profile(user_id)
        if profile["starter"]:
            raise TcgError("You already claimed your starter deck. Open packs with `/tcg pack` to grow your collection.")
        cards = list(ARCHETYPES[archetype]["cards"])
        collection = Counter(profile["collection"])
        collection.update(cards)
        profile["collection"] = dict(collection)
        profile["decks"][ARCHETYPES[archetype]["name"]] = cards
        profile["active_deck"] = ARCHETYPES[archetype]["name"]
        profile["starter"] = archetype
        self.repo.save_profile(user_id, profile)
        return profile

    def save_deck(self, user_id, deck_name: str, cards: list[str]) -> list[str]:
        """Validate rules and ownership; save and activate if legal. Returns the problems (empty = saved)."""
        deck_name = deck_name.strip()[:40] or "Deck"
        problems = validate_deck(cards)
        profile = self.repo.profile(user_id)
        owned = profile["collection"]
        for cid, n in Counter(cards).items():
            if n > owned.get(cid, 0):
                problems.append(f"You own {owned.get(cid, 0)} × {CARD_BY_ID[cid].name} but the deck uses {n}.")
        if not problems:
            if deck_name not in profile["decks"] and len(profile["decks"]) >= 10:
                return ["You can keep at most 10 decks; overwrite or delete one first."]
            profile["decks"][deck_name] = list(cards)
            profile["active_deck"] = deck_name
            self.repo.save_profile(user_id, profile)
        return problems

    def use_deck(self, user_id, deck_name: str) -> None:
        profile = self.repo.profile(user_id)
        if deck_name not in profile["decks"]:
            raise TcgError("You have no deck with that name.")
        profile["active_deck"] = deck_name
        self.repo.save_profile(user_id, profile)

    def active_deck(self, user_id) -> list[str]:
        profile = self.repo.profile(user_id)
        if not profile["active_deck"] or profile["active_deck"] not in profile["decks"]:
            raise TcgError("You don't have a deck yet. Claim one with `/tcg starter`.")
        cards = profile["decks"][profile["active_deck"]]
        problems = validate_deck(cards)
        if problems:
            raise TcgError("Your active deck is not legal: " + problems[0])
        return list(cards)

    def open_pack(self, user_id) -> list[str]:
        price = self.terms.pack_price
        account = self.users.get(user_id)
        if account["wallet"] < price:
            raise TcgError(f"A Cloudfall pack costs {price} in your wallet.")
        self.users.update(user_id, wallet=account["wallet"] - price)
        pool, weights = [], []
        for card in CARDS:
            if card.unique:
                w = 1
            elif card.kind == CHARACTER:
                w = {5: 1, 4: 2, 3: 4}.get(card.grade, 6)
            else:
                w = 5
            pool.append(card.id)
            weights.append(w)
        pulled = self.rng.choices(pool, weights=weights, k=self.terms.pack_size)
        profile = self.repo.profile(user_id)
        collection = Counter(profile["collection"])
        collection.update(pulled)
        profile["collection"] = dict(collection)
        self.repo.save_profile(user_id, profile)
        return pulled

    def forget_me(self, user_id) -> bool:
        """Delete the caller's Cloudfall profile (collection, decks, rating, history)."""
        if self.match_for(user_id):
            raise TcgError("Finish or forfeit your match first.")
        return self.repo.delete_profile(user_id)

    def ladder(self, limit: int = 10) -> list[tuple[str, dict]]:
        rows = [(uid, p) for uid, p in self.repo.all_profiles().items() if p.get("wins", 0) + p.get("losses", 0) > 0]
        rows.sort(key=lambda row: -row[1].get("rating", 1000))
        return rows[:limit]

    # ================================================================== match lifecycle
    def match_for(self, user_id) -> Optional[Match]:
        for match in self.matches.values():
            if match.seat_of(user_id) is not None:
                return match
        return None

    def _new_match(self, seats, names, decks, mode, **extra) -> Match:
        for seat in seats:
            if not str(seat).startswith(AI_SEAT_PREFIX) and self.match_for(seat):
                raise TcgError("Someone in this match is already playing a Cloudfall match.")
        seed = self.rng.randrange(1, 2**31)
        record = {"id": uuid.uuid4().hex[:10], "mode": mode, "seats": [str(s) for s in seats], "names": list(names),
                  "decks": [list(d) for d in decks], "seed": seed, "level": extra.pop("level", 3), "keys": [],
                  "created": self.clock.now().isoformat(timespec="seconds"),
                  "last_action": self.clock.now().isoformat(timespec="seconds"), "timeouts": [0, 0], **extra}
        game = E.new_game(record["decks"], record["names"], seed, level=record["level"])
        record["first"] = game.first
        match = Match(record, game)
        self.matches[match.id] = match
        self.repo.save_match(match.id, record)
        return match

    def create_pvp(self, user_a, name_a, user_b, name_b, *, coach: bool = False, channel_id=None) -> Match:
        if str(user_a) == str(user_b):
            raise TcgError("You can't challenge yourself. Try `/tcg practice`.")
        decks = [self.active_deck(user_a), self.active_deck(user_b)]
        return self._new_match([user_a, user_b], [name_a, name_b], decks, "pvp", coach=coach, channel_id=channel_id)

    def create_practice(self, user_id, name, difficulty: str = "normal", opponent: Optional[str] = None,
                        channel_id=None) -> Match:
        deck = self.active_deck(user_id)
        key = opponent if opponent in ARCHETYPES else self.rng.choice(sorted(ARCHETYPES))
        ai_name = f"Oracle ({ARCHETYPES[key]['name']}, {difficulty})"
        return self._new_match([user_id, f"{AI_SEAT_PREFIX}{difficulty}"], [name, ai_name],
                               [deck, list(ARCHETYPES[key]["cards"])], "practice", coach=True, difficulty=difficulty,
                               channel_id=channel_id)

    def get(self, match_id: str) -> Optional[Match]:
        return self.matches.get(match_id)

    def restore(self) -> int:
        """Rebuild every persisted live match by deterministic replay. Corrupt records are dropped and logged."""
        restored = 0
        for match_id, record in self.repo.live_matches().items():
            try:
                game = E.replay(record["decks"], record["names"], record["seed"], record["keys"], level=record.get("level", 3),
                                first=record.get("first"))
                if game.over:
                    self.repo.delete_match(match_id)
                    continue
                self.matches[match_id] = Match(record, game)
                restored += 1
            except Exception:
                log.exception("could not restore match %s; discarding it", match_id)
                self.repo.delete_match(match_id)
        return restored

    def _persist(self, match: Match) -> None:
        match.record["last_action"] = self.clock.now().isoformat(timespec="seconds")
        self.repo.save_match(match.id, match.record)

    def _apply(self, match: Match, key: str) -> None:
        E.apply(match.game, key)                     # raises IllegalAction; state untouched on refusal
        match.record["keys"].append(key)

    async def act(self, match_id: str, user_id, key: str, version: int) -> MoveResult:
        match = self.matches.get(match_id)
        if match is None:
            raise TcgError("That match is over.")
        async with match.lock:
            seat = match.seat_of(user_id)
            if seat is None:
                raise TcgError("You're not playing in this match.")
            if version != match.version:
                raise TcgError("That menu is out of date. Press **Act** again for your current options.")
            if E.decider(match.game) != seat:
                raise TcgError("It's not your decision right now.")
            before = len(match.game.log)
            try:
                self._apply(match, key)
            except E.IllegalAction:
                raise TcgError("That move isn't legal any more. Press **Act** again.")
            match.record["timeouts"][seat] = 0
            self._persist(match)
            new_log = match.game.log[before:]
            finished = self._finish_if_over(match)
            return MoveResult(match, new_log, finished)

    async def run_ai(self, match: Match, max_moves: int = 200) -> MoveResult:
        """Let the AI seat act until a human must decide. Each AI move is still applied through the engine."""
        before = len(match.game.log)
        async with match.lock:
            moves = 0
            while not match.game.over and match.decider_is_ai and moves < max_moves:
                seat = E.decider(match.game)
                difficulty = str(match.record["seats"][seat])[len(AI_SEAT_PREFIX):]
                acts = E.legal_actions(match.game)
                if len(acts) == 1:
                    key = acts[0].key
                elif self.oracle is not None:
                    key = await self.oracle.choose_move(match.game, difficulty)
                else:
                    key = acts[0].key
                self._apply(match, key)
                moves += 1
            self._persist(match)
            finished = self._finish_if_over(match)
            return MoveResult(match, match.game.log[before:], finished)

    async def forfeit(self, match_id: str, user_id) -> dict:
        match = self.matches.get(match_id)
        if match is None:
            raise TcgError("That match is over.")
        async with match.lock:
            seat = match.seat_of(user_id)
            if seat is None:
                raise TcgError("You're not playing in this match.")
            E._finish(match.game, match.game.other(seat), f"{match.record['names'][seat]} forfeited")
            return self._finish_if_over(match)

    async def sweep_timeouts(self) -> list[MoveResult]:
        """Auto-move idle human players with the safest option; forfeit after `max_timeouts` in a row."""
        out = []
        now = self.clock.now()
        for match in list(self.matches.values()):
            seat = E.decider(match.game)
            if seat is None or match.is_ai(seat):
                continue
            idle = (now - datetime.fromisoformat(match.record["last_action"])).total_seconds()
            if idle < self.terms.turn_timeout_s:
                continue
            async with match.lock:
                before = len(match.game.log)
                match.record["timeouts"][seat] += 1
                if match.record["timeouts"][seat] >= self.terms.max_timeouts:
                    E._finish(match.game, match.game.other(seat), f"{match.record['names'][seat]} timed out")
                else:
                    self._apply(match, safe_default(E.legal_actions(match.game)))
                    match.game.say(f"⏰ {match.record['names'][seat]} took too long; Rudhabot chose a safe default.")
                self._persist(match)
                out.append(MoveResult(match, match.game.log[before:], self._finish_if_over(match)))
        return out

    # ================================================================== results
    def _finish_if_over(self, match: Match) -> Optional[dict]:
        g = match.game
        if not g.over:
            return None
        self.matches.pop(match.id, None)
        self.repo.delete_match(match.id)
        record = match.record
        summary = {"id": match.id, "mode": record["mode"], "winner": g.winner, "reason": g.end_reason,
                   "names": record["names"], "rounds": g.round, "rewards": {}, "rating": {}}
        humans = [(seat, uid) for seat, uid in enumerate(record["seats"]) if not uid.startswith(AI_SEAT_PREFIX)]
        rated = record["mode"] == "pvp" and not record.get("coach") and len(humans) == 2
        profiles = {uid: self.repo.profile(uid) for _, uid in humans}
        if rated:
            (s0, u0), (s1, u1) = humans
            r0, r1 = profiles[u0]["rating"], profiles[u1]["rating"]
            expected0 = 1 / (1 + 10 ** ((r1 - r0) / 400))
            score0 = 0.5 if g.winner == -1 else float(g.winner == s0)
            delta = round(self.terms.elo_k * (score0 - expected0))
            profiles[u0]["rating"], profiles[u1]["rating"] = r0 + delta, r1 - delta
            summary["rating"] = {u0: delta, u1: -delta}
        for seat, uid in humans:
            p = profiles[uid]
            if g.winner == -1:
                p["draws"] += 1
            elif g.winner == seat:
                p["wins"] += 1
            else:
                p["losses"] += 1
            p["history"] = (p["history"] + [{"id": match.id, "mode": record["mode"], "won": g.winner == seat,
                                              "vs": record["names"][1 - seat], "rounds": g.round}])[-20:]
        if rated and g.winner in (0, 1):
            winner_uid = record["seats"][g.winner]
            loser_uid = record["seats"][1 - g.winner]
            reward = self._reward(profiles[winner_uid], loser_uid)
            if reward:
                self.users.credit_wallet(winner_uid, reward)
                summary["rewards"][winner_uid] = reward
        for uid, p in profiles.items():
            self.repo.save_profile(uid, p)
        summary["record"] = record
        return summary

    def _reward(self, profile: dict, opponent_uid: str) -> int:
        today = self.clock.now().date().isoformat()
        rewards = profile["rewards"]
        if rewards.get("day") != today:
            rewards.update(day=today, count=0, opponents={})
        if rewards["count"] >= self.terms.daily_reward_wins:
            return 0
        if rewards["opponents"].get(opponent_uid, 0) >= self.terms.same_opponent_rewards:
            return 0
        rewards["count"] += 1
        rewards["opponents"][opponent_uid] = rewards["opponents"].get(opponent_uid, 0) + 1
        return self.terms.win_reward


def safe_default(acts: list) -> str:
    """The most conservative legal move: pass / skip / keep / don't attack, else the first option."""
    for prefix in SAFE_PREFIXES:
        for act in acts:
            if act.key.startswith(prefix):
                return act.key
    skip = next((a for a in acts if a.key.endswith(":skip")), None)
    return (skip or acts[0]).key

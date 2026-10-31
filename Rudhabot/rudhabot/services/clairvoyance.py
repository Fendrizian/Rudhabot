"""The Clairvoyance service: Rudhabot's in-house ML, served to the rest of the bot.

Everything here is *advisory*. The Clairvoyance ranks moves the engine already says are legal, estimates win probability and
compares cards; the TCG service applies moves only through `engine.apply`, which re-validates them.

Serving: inference runs in a worker thread (`asyncio.to_thread`) behind a semaphore and a timeout, so the Discord event
loop never blocks. If the model is disabled, missing, has a schema mismatch, errors or times out, the service falls back
to the deterministic heuristic evaluator and counts the fallback.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from rudhabot.ml.features import encoder_for
from rudhabot.ml.nn import _Net
from rudhabot.ml.model_store import ArtifactError, ModelStore
from rudhabot.ml.policy import BlendEvaluator, HeuristicEvaluator, NetEvaluator, choose, score_actions, terminal_value
from rudhabot.tcg import engine as E
from rudhabot.tcg.cards import CARD_BY_ID, CARD_IDS, CARD_INDEX
from rudhabot.tcg.decks import ARCHETYPES
from rudhabot.tcg.state import Game

log = logging.getLogger("rudhabot.clairvoyance")

DIFFICULTIES = {          # (softmax temperature over win-probabilities, ε random-move rate)
    "easy": (0.08, 0.25),
    "normal": (0.02, 0.05),
    "hard": (0.0, 0.0),
}


@dataclass(frozen=True)
class Hint:
    label: str
    win: float
    delta: float


class ClairvoyanceService:
    def __init__(self, models_dir: str = "models", enabled: bool = True, version: Optional[str] = None,
                 timeout: float = 4.0, concurrency: int = 2, rng: Optional[random.Random] = None):
        self.store = ModelStore(models_dir)
        self.timeout = timeout
        self.rng = rng or random.Random()
        self._sem = asyncio.Semaphore(concurrency)
        self.heuristic = HeuristicEvaluator()
        self.net: Optional[_Net] = None
        self.load_error: Optional[str] = None
        self.stats = Counter()
        self.latency_ms: deque = deque(maxlen=500)
        self.enabled = enabled
        if enabled:
            self.reload(version)
        else:
            self.load_error = "disabled by configuration (CLAIRVOYANCE_ENABLED=0)"

    # ------------------------------------------------------------------ lifecycle
    def reload(self, version: Optional[str] = None) -> bool:
        try:
            self.net = self.store.load_current(version)
            self.load_error = None
            log.info("clairvoyance model %s loaded (%d params)", self.version, self.net.n_params())
            return True
        except (ArtifactError, OSError, ValueError, KeyError) as error:
            self.net = None
            self.load_error = str(error)
            log.warning("clairvoyance model unavailable, using heuristic fallback: %s", error)
            return False

    @property
    def version(self) -> str:
        return self.net.meta.get("version", "?") if self.net else self.heuristic.name

    @property
    def evaluator(self):
        """Calibrated probabilities (win bar, hints, turning points): the network alone."""
        return NetEvaluator(self.net) if self.net is not None else self.heuristic

    @property
    def move_evaluator(self):
        """Move choice for the AI opponent: the network blended with the heuristic prior (best measured policy, see
        models/manifest.json `serving`)."""
        return BlendEvaluator(self.net, 0.5) if self.net is not None else self.heuristic

    @property
    def ready(self) -> bool:
        return self.net is not None

    def status(self) -> dict:
        lat = np.array(self.latency_ms) if self.latency_ms else np.array([0.0])
        entry = self.store.manifest().get("versions", {}).get(self.version, {}) if self.net else {}
        return {"model": self.version, "ready": self.ready, "load_error": self.load_error, "serving": entry.get("serving"),
                "calls": dict(self.stats), "latency_ms_p50": float(np.percentile(lat, 50)),
                "latency_ms_p95": float(np.percentile(lat, 95)),
                # "model" in v2 manifests, "oracle" in the legacy oracle-v1 entry
                "metrics": entry.get("metrics", {}).get("model") or entry.get("metrics", {}).get("oracle"),
                "strength": entry.get("strength"),
                "params": entry.get("params")}

    # ------------------------------------------------------------------ guarded execution
    async def _run(self, task: str, fn, fallback, timeout: Optional[float] = None):
        start = time.perf_counter()
        self.stats[task] += 1
        try:
            async with self._sem:
                result = await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout or self.timeout)
        except Exception as error:                     # timeout, model error: degrade, never break the game
            self.stats[f"{task}_fallback"] += 1
            log.warning("clairvoyance %s failed (%s: %s); using fallback", task, type(error).__name__, error)
            result = fallback()
        self.latency_ms.append((time.perf_counter() - start) * 1000)
        log.debug("clairvoyance task=%s model=%s ms=%.1f", task, self.version, self.latency_ms[-1])
        return result

    # ------------------------------------------------------------------ products
    def _value(self, g: Game, seat: int, evaluator=None) -> float:
        if g.over:
            return terminal_value(g, seat)
        return float((evaluator or self.evaluator).values([g], seat)[0])

    def public_win_probability_sync(self, g: Game, seat: int = 0) -> float:
        """Spectator-safe estimate: averages both players' views with *both hands hidden*, so the number shown
        publicly reveals nothing about anyone's hand."""
        if g.over:
            return terminal_value(g, seat)
        if self.net is None:
            return float(self.heuristic.values([g], seat)[0])
        views = []
        for persp in (seat, g.other(seat)):
            arrays = list(encoder_for(self.net.ARCH)[0](g, persp))
            arrays[-1] = arrays[-1].copy()
            arrays[-1][0, :] = 0                           # bags are last for every architecture: hide the hand
            p = float(self.net.predict(*[a[None] for a in arrays])[0])
            views.append(p if persp == seat else 1 - p)
        return float(np.mean(views))

    async def public_win_probability(self, g: Game, seat: int = 0) -> float:
        snapshot = g.clone()
        return await self._run("winprob", lambda: self.public_win_probability_sync(snapshot, seat),
                               lambda: float(self.heuristic.values([snapshot], seat)[0]))

    def choose_sync(self, g: Game, difficulty: str = "normal"):
        temperature, epsilon = DIFFICULTIES.get(difficulty, DIFFICULTIES["normal"])
        return choose(g, self.move_evaluator, self.rng, temperature, epsilon)

    async def choose_move(self, g: Game, difficulty: str = "normal") -> str:
        """Return the key of the AI's chosen legal action (the caller applies it through the engine)."""
        snapshot = g.clone()
        act = await self._run("move", lambda: self.choose_sync(snapshot, difficulty),
                              lambda: choose(snapshot, self.heuristic, self.rng))
        return act.key

    def hints_sync(self, g: Game, k: int = 3) -> list[Hint]:
        seat = E.decider(g)
        now = self._value(g, seat)
        ranked = score_actions(g, self.evaluator, self.rng)
        return [Hint(act.label, value, value - now) for act, value in ranked[:k]]

    async def hints(self, g: Game, k: int = 3) -> list[Hint]:
        snapshot = g.clone()
        return await self._run("hint", lambda: self.hints_sync(snapshot, k), lambda: [])

    # ------------------------------------------------------------------ cards and decks
    def similar_cards(self, cid: str, k: int = 3) -> list[tuple[str, float]]:
        """Nearest cards in the learned embedding space (cards the model treats as playing similar roles)."""
        if self.net is None or cid not in CARD_INDEX:
            return []
        emb = self.net.card_embeddings()
        sims = emb @ emb[CARD_INDEX[cid]]
        order = [i for i in np.argsort(-sims) if CARD_IDS[i] != cid]
        return [(CARD_IDS[i], float(sims[i])) for i in order[:k]]

    def card_values(self) -> dict:
        if self.net is None:
            return {}
        entry = self.store.manifest().get("versions", {}).get(self.version, {})
        return entry.get("card_values", {})

    def suggest_swaps(self, deck: list[str], owned: dict[str, int], k: int = 3) -> list[tuple[str, str, float]]:
        """Pairs (cut, add, estimated gain) from the learned card values, respecting legality and the collection."""
        from rudhabot.tcg.decks import validate_deck
        values = self.card_values()
        if not values:
            return []
        worth = lambda c: values.get(c, {}).get("hand", 0.0) + values.get(c, {}).get("board", 0.0)
        counts = Counter(deck)
        candidates = sorted({c for c, n in owned.items() if n > counts.get(c, 0)}, key=worth, reverse=True)
        cuts = sorted(set(deck), key=worth)
        out = []
        for cut in cuts:
            for add in candidates:
                if worth(add) <= worth(cut) or any(a == add for _, a, _ in out):
                    continue
                trial = list(deck)
                trial.remove(cut)
                trial.append(add)
                if not validate_deck(trial):
                    out.append((cut, add, worth(add) - worth(cut)))
                    break
            if len(out) >= k:
                break
        return out

    def gauntlet_sync(self, deck: list[str], games_per_archetype: int = 2, budget_s: float = 20.0, seed: int = 0) -> dict:
        """Play `deck` against each archetype (seat-swapped) with the Clairvoyance piloting both sides; time-bounded."""
        from rudhabot.ml.policy import SearchPolicy
        policy = SearchPolicy(self.move_evaluator, temperature=0.0)
        rng = random.Random(seed)
        start = time.perf_counter()
        results = {}
        for key, arche in ARCHETYPES.items():
            score = played = 0.0
            for i in range(games_per_archetype):
                for my_seat in (0, 1):
                    if time.perf_counter() - start > budget_s:
                        break
                    decks = [list(deck), list(arche["cards"])] if my_seat == 0 else [list(arche["cards"]), list(deck)]
                    g = E.new_game(decks, ["you", key], seed=seed * 1000 + i * 2 + my_seat)
                    steps = 0
                    while not g.over and steps < 3000:
                        acts = E.legal_actions(g)
                        E.apply(g, acts[0].key if len(acts) == 1 else policy.pick(g, rng).key)
                        steps += 1
                    played += 1
                    score += 0.5 if g.winner in (-1, None) else float(g.winner == my_seat)
            if played:
                results[key] = {"score": score / played, "games": int(played)}
        return {"matchups": results, "seconds": round(time.perf_counter() - start, 1), "model": self.version}

    async def gauntlet(self, deck: list[str], games_per_archetype: int = 2) -> dict:
        return await self._run("gauntlet", lambda: self.gauntlet_sync(deck, games_per_archetype),
                               lambda: {"matchups": {}, "seconds": 0, "model": "unavailable"}, timeout=60.0)

    def turning_points_sync(self, g_final: Game, decks, names, seed, keys, level=3, first=None, k=2) -> list[dict]:
        """Replay a finished match and find the moves with the largest public win-probability swing for player 0."""
        g = E.new_game(decks, names, seed, level=level, first=first)
        prev = self.public_win_probability_sync(g, 0)
        swings = []
        for i, key in enumerate(keys):
            seat = E.decider(g)
            label = next((a.label for a in E.legal_actions(g) if a.key == key), key)
            E.apply(g, key)
            if i % 2 and not g.over and i != len(keys) - 1:
                continue                          # sample every other move to keep this cheap
            now = self.public_win_probability_sync(g, 0)
            swings.append({"move": i, "round": g.round, "player": names[seat] if seat is not None else "?",
                           "label": label, "before": prev, "after": now, "swing": now - prev})
            prev = now
        swings.sort(key=lambda s: -abs(s["swing"]))
        return swings[:k]

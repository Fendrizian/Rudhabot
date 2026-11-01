"""Self-play data generation for Clairvoyance. The only training data is games the engine plays against itself: no user
data is ever used.

Each game pairs two decks (an archetype, or with probability `mutate` a legal random mutation of one, for diversity)
and two policies. A fraction of decision points are recorded from *both* players' perspectives. Targets:
  * Monte-Carlo: the final result (1 win / 0 loss / 0.5 draw), or
  * TD(λ): λ-returns that blend the final result with the acting network's estimate of each afterstate
    (G_t = (1-λ)·V(s_{t+1}) + λ·G_{t+1}), which lowers target variance once a calibrated network is playing.
Seeds define the split: train and test games never share a seed, so no game leaks across the split.
"""
from __future__ import annotations

import multiprocessing as mp
import random
from dataclasses import dataclass
from typing import Optional

import numpy as np

from rudhabot.ml.features import encoder_for
from rudhabot.ml.nn import _Net, load_net
from rudhabot.ml.policy import BlendEvaluator, HeuristicEvaluator, NetEvaluator, RandomPolicy, SearchPolicy
from rudhabot.tcg import engine as E
from rudhabot.tcg.cards import CARDS
from rudhabot.tcg.decks import ARCHETYPES, validate_deck

ARCHETYPE_KEYS = tuple(ARCHETYPES)
COMPRESS = {"v1": (np.float16, np.uint8), "v2": (np.float16, np.float16, np.int16, np.uint8, np.uint8)}


@dataclass(frozen=True)
class PolicySpec:
    """Picklable description of a policy, so worker processes can rebuild it."""
    kind: str                       # "random" | "heuristic" | "net" | "blend"
    model_path: Optional[str] = None
    epsilon: float = 0.0
    temperature: float = 0.0
    weight: float = 0.5             # heuristic weight for "blend"


@dataclass(frozen=True)
class RecordSpec:
    arch: str = "v2"
    rate: float = 0.0               # fraction of decision points recorded
    td_lambda: float = 1.0          # 1.0 = pure Monte-Carlo targets
    mutate: float = 0.0             # probability that each deck is a mutated archetype


_NET_CACHE: dict[str, _Net] = {}


def build_policy(spec: PolicySpec):
    if spec.kind == "random":
        return RandomPolicy()
    if spec.kind == "heuristic":
        return SearchPolicy(HeuristicEvaluator(), spec.temperature, spec.epsilon)
    net = _NET_CACHE.get(spec.model_path)
    if net is None:
        net = _NET_CACHE[spec.model_path] = load_net(spec.model_path)
    if spec.kind == "blend":
        return SearchPolicy(BlendEvaluator(net, spec.weight), spec.temperature, spec.epsilon)
    return SearchPolicy(NetEvaluator(net), spec.temperature, spec.epsilon)


def deck_pair(seed: int) -> tuple[str, str]:
    r = random.Random(seed * 7919 + 13)
    return r.choice(ARCHETYPE_KEYS), r.choice(ARCHETYPE_KEYS)


POOL = [c.id for c in CARDS]


def mutate_deck(deck: list[str], rng: random.Random, swaps: int = 6) -> list[str]:
    """A legal deck that differs from `deck` in up to `swaps` cards (falls back to the original)."""
    for _ in range(30):
        trial = list(deck)
        for _ in range(swaps):
            trial.pop(rng.randrange(len(trial)))
            trial.append(rng.choice(POOL))
        if not validate_deck(trial):
            return trial
    return list(deck)


def game_decks(seed: int, mutate: float) -> list[list[str]]:
    a, b = deck_pair(seed)
    r = random.Random(seed * 104729 + 7)
    decks = []
    for key in (a, b):
        deck = list(ARCHETYPES[key]["cards"])
        if mutate and r.random() < mutate:
            deck = mutate_deck(deck, r, swaps=r.randint(3, 10))
        decks.append(deck)
    return decks


def play_game(seed: int, specs: tuple[PolicySpec, PolicySpec], rec: RecordSpec = RecordSpec()):
    """Play one game. Returns (winner, rounds, records); records = [(arrays, seat, step, baseline P(win))] plus the
    per-step afterstate values used for TD targets."""
    rng = random.Random(seed)
    g = E.new_game(game_decks(seed, rec.mutate), ["P0", "P1"], seed=seed)
    policies = [build_policy(s) for s in specs]
    td_capable = [s.kind in ("net", "blend") for s in specs]
    encode_one = encoder_for(rec.arch)[0]
    baseline = HeuristicEvaluator()
    records, values = [], []          # values[t] = afterstate value of step t from seat 0's view, or None
    steps = 0
    while not g.over and steps < 4000:
        seat = E.decider(g)
        acts = E.legal_actions(g)
        if rec.rate and len(acts) > 1 and rng.random() < rec.rate:
            for persp in (0, 1):
                arrays = encode_one(g, persp)
                packed = tuple(a.astype(t) for a, t in zip(arrays, COMPRESS[rec.arch]))
                records.append((packed, persp, steps, baseline._value(g, persp)))
        value = None
        if len(acts) == 1:
            act = acts[0]
        else:
            act = policies[seat].pick(g, rng)
            v = getattr(policies[seat], "last_value", None)
            if v is not None and td_capable[seat]:
                value = v if seat == 0 else 1.0 - v
        values.append(value)
        E.apply(g, act.key)
        steps += 1
    winner = g.winner if g.over else -1
    return winner, g.round, records, values


def lambda_returns(values: list, outcome0: float, lam: float) -> list[float]:
    """G_t for every step (seat 0's view): G_T = outcome; G_t = (1-λ)·v_t + λ·G_{t+1} where v_t is known."""
    out = [0.0] * len(values)
    g = outcome0
    for t in range(len(values) - 1, -1, -1):
        v = values[t]
        if v is not None and lam < 1.0:
            g = (1 - lam) * v + lam * g
        out[t] = g
    return out


def _worker(job):
    seed, specs, rec = job
    winner, rounds, records, values = play_game(seed, specs, rec)
    if not records:
        return winner, rounds, None
    outcome0 = 0.5 if winner == -1 else float(winner == 0)
    returns = lambda_returns(values, outcome0, rec.td_lambda)
    n_arrays = len(records[0][0])
    arrays = tuple(np.stack([r[0][i] for r in records]) for i in range(n_arrays))
    seats = np.array([r[1] for r in records], dtype=np.int8)
    targets = np.array([returns[r[2]] if r[1] == 0 else 1.0 - returns[r[2]] for r in records], dtype=np.float32)
    outcomes = np.where(seats == winner, 1.0, 0.0).astype(np.float32) if winner != -1 else np.full(len(seats), 0.5, np.float32)
    game_ids = np.full(len(seats), seed, dtype=np.int64)
    baseline = np.array([r[3] for r in records], dtype=np.float32)
    return winner, rounds, (arrays, targets, outcomes, game_ids, baseline)


def generate(seeds, spec_for_seed, rec: RecordSpec, processes: int = 0, log=print):
    """Play every seed in parallel. Returns (inputs tuple, targets, outcomes, game_ids, baseline) and per-game results.

    `targets` are the training labels (λ-returns); `outcomes` are the true final results, used for every evaluation."""
    jobs = [(s, spec_for_seed(s), rec) for s in seeds]
    processes = processes or max(1, mp.cpu_count())
    if not jobs:
        raise ValueError("generate() needs at least one seed")
    chunks, results = [], []
    with mp.get_context("fork").Pool(processes) as pool:
        for i, (winner, rounds, data) in enumerate(pool.imap(_worker, jobs, chunksize=4)):
            results.append((jobs[i][0], winner, rounds))
            if data is not None:
                chunks.append(data)
            if (i + 1) % max(1, len(jobs) // 5) == 0:
                log(f"  {i + 1}/{len(jobs)} games")
    n_arrays = len(chunks[0][0])
    # kept in their compact recording dtypes (float16 / int16 / uint8): ~4x less memory; networks cast per batch
    inputs = tuple(np.concatenate([c[0][i] for c in chunks]) for i in range(n_arrays))
    cat = lambda k: np.concatenate([c[k] for c in chunks])
    return (inputs, cat(1), cat(2), cat(3), cat(4)), results


def arena(spec_a: PolicySpec, spec_b: PolicySpec, seeds, processes: int = 0, mutate: float = 0.0) -> dict:
    """Play A vs B on every seed twice (A as seat 0, then as seat 1, same decks and shuffles). A's score: win=1, draw=.5."""
    rec = RecordSpec(rate=0.0, mutate=mutate)
    jobs = []
    for s in seeds:
        jobs.append((s, (spec_a, spec_b), rec))
        jobs.append((s, (spec_b, spec_a), rec))
    processes = processes or max(1, mp.cpu_count())
    score = wins = draws = 0.0
    rounds = []
    with mp.get_context("fork").Pool(processes) as pool:
        for i, (winner, r, _) in enumerate(pool.imap(_worker, jobs, chunksize=4)):
            a_seat = 0 if i % 2 == 0 else 1
            rounds.append(r)
            if winner == -1:
                draws += 1; score += 0.5
            elif winner == a_seat:
                wins += 1; score += 1
    n = len(jobs)
    rate = score / n
    z = 1.96
    centre = (rate + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return {"games": n, "score": rate, "wins": int(wins), "draws": int(draws), "ci95": [float(centre - half), float(centre + half)],
            "avg_rounds": float(np.mean(rounds))}

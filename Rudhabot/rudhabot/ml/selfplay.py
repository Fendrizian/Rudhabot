"""Self-play data generation. The only training data is games the engine plays against itself: no user data is ever used.

Each game pairs two archetype decks (chosen by the game seed) and two policies. A fraction of decision points are
recorded from *both* players' perspectives; once the game ends every record is labelled 1 (that player won), 0 (lost)
or 0.5 (draw). Seeds define the split: train and test games never share a seed, so no game leaks across the split.
"""
from __future__ import annotations

import multiprocessing as mp
import random
from dataclasses import dataclass
from typing import Optional

import numpy as np

from rudhabot.ml.features import encode
from rudhabot.ml.nn import OracleNet
from rudhabot.ml.policy import BlendEvaluator, HeuristicEvaluator, NetEvaluator, RandomPolicy, SearchPolicy
from rudhabot.tcg import engine as E
from rudhabot.tcg.decks import ARCHETYPES

ARCHETYPE_KEYS = tuple(ARCHETYPES)


@dataclass(frozen=True)
class PolicySpec:
    """Picklable description of a policy, so worker processes can rebuild it."""
    kind: str                       # "random" | "heuristic" | "net" | "blend"
    model_path: Optional[str] = None
    epsilon: float = 0.0
    temperature: float = 0.0
    weight: float = 0.5             # heuristic weight for "blend"


_NET_CACHE: dict[str, OracleNet] = {}


def build_policy(spec: PolicySpec):
    if spec.kind == "random":
        return RandomPolicy()
    if spec.kind == "heuristic":
        return SearchPolicy(HeuristicEvaluator(), spec.temperature, spec.epsilon)
    net = _NET_CACHE.get(spec.model_path)
    if net is None:
        net = _NET_CACHE[spec.model_path] = OracleNet.load(spec.model_path)
    if spec.kind == "blend":
        return SearchPolicy(BlendEvaluator(net, spec.weight), spec.temperature, spec.epsilon)
    return SearchPolicy(NetEvaluator(net), spec.temperature, spec.epsilon)


def deck_pair(seed: int) -> tuple[str, str]:
    r = random.Random(seed * 7919 + 13)
    return r.choice(ARCHETYPE_KEYS), r.choice(ARCHETYPE_KEYS)


def play_game(seed: int, specs: tuple[PolicySpec, PolicySpec], record: float = 0.0):
    """Play one game. Returns (winner, rounds, decks, records); records = [(dense, bags, seat, baseline P(win))]."""
    rng = random.Random(seed)
    a, b = deck_pair(seed)
    g = E.new_game([list(ARCHETYPES[a]["cards"]), list(ARCHETYPES[b]["cards"])], ["P0", "P1"], seed=seed)
    policies = [build_policy(s) for s in specs]
    baseline = HeuristicEvaluator()
    records = []
    steps = 0
    while not g.over and steps < 4000:
        seat = E.decider(g)
        acts = E.legal_actions(g)
        if record and len(acts) > 1 and rng.random() < record:
            for persp in (0, 1):
                dense, bags = encode(g, persp)
                records.append((dense.astype(np.float16), bags.astype(np.uint8), persp, baseline._value(g, persp)))
        act = acts[0] if len(acts) == 1 else policies[seat].pick(g, rng)
        E.apply(g, act.key)
        steps += 1
    winner = g.winner if g.over else -1
    return winner, g.round, (a, b), records


def _worker(job):
    seed, specs, record = job
    winner, rounds, decks, records = play_game(seed, specs, record)
    if not records:
        return winner, rounds, decks, None
    dense = np.stack([r[0] for r in records])
    bags = np.stack([r[1] for r in records])
    seats = np.array([r[2] for r in records], dtype=np.int8)
    if winner == -1:
        labels = np.full(len(seats), 0.5, dtype=np.float32)
    else:
        labels = (seats == winner).astype(np.float32)
    game_ids = np.full(len(seats), seed, dtype=np.int64)
    baseline = np.array([r[3] for r in records], dtype=np.float32)
    return winner, rounds, decks, (dense, bags, labels, game_ids, baseline)


def generate(seeds, spec_for_seed, record: float = 0.25, processes: int = 0, log=print):
    """Play every seed (in parallel) and return stacked arrays plus per-game results."""
    jobs = [(s, spec_for_seed(s), record) for s in seeds]
    processes = processes or max(1, mp.cpu_count())
    chunks, results = [], []
    with mp.get_context("fork").Pool(processes) as pool:
        for i, (winner, rounds, decks, data) in enumerate(pool.imap(_worker, jobs, chunksize=4)):
            results.append((jobs[i][0], winner, rounds, decks))
            if data is not None:
                chunks.append(data)
            if (i + 1) % max(1, len(jobs) // 10) == 0:
                log(f"  {i + 1}/{len(jobs)} games")
    dense = np.concatenate([c[0] for c in chunks]).astype(np.float32)
    bags = np.concatenate([c[1] for c in chunks]).astype(np.float32)
    labels = np.concatenate([c[2] for c in chunks])
    game_ids = np.concatenate([c[3] for c in chunks])
    baseline = np.concatenate([c[4] for c in chunks])
    return (dense, bags, labels, game_ids, baseline), results


def arena(spec_a: PolicySpec, spec_b: PolicySpec, seeds, processes: int = 0) -> dict:
    """Play A vs B on every seed twice (A as seat 0, then as seat 1, same decks and shuffles). A's score: win=1, draw=.5."""
    jobs = []
    for s in seeds:
        jobs.append((s, (spec_a, spec_b), 0.0))
        jobs.append((s, (spec_b, spec_a), 0.0))
    processes = processes or max(1, mp.cpu_count())
    score = wins = draws = 0.0
    rounds = []
    with mp.get_context("fork").Pool(processes) as pool:
        for i, (winner, r, _, _) in enumerate(pool.imap(_worker, jobs, chunksize=4)):
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

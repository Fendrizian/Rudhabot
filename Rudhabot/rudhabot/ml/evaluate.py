"""Evaluation for the Cloudfall Clairvoyance: probabilistic quality (log-loss, Brier, calibration, AUC), playing strength
(seat-swapped arenas with Wilson 95% intervals) and serving latency.

    python -m rudhabot.ml.evaluate                  # evaluate the current artifact
    python -m rudhabot.ml.evaluate --games 200
"""
from __future__ import annotations

import argparse
import json
import random
import time

import numpy as np


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-7, 1 - 1e-7)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error: |mean prediction - observed rate|, weighted over equal-width bins."""
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    total = 0.0
    for b in range(bins):
        mask = idx == b
        if mask.any():
            total += mask.mean() * abs(p[mask].mean() - y[mask].mean())
    return float(total)


def auc(p: np.ndarray, y: np.ndarray) -> float:
    """ROC AUC on decisive labels (draw rows are excluded), via the rank-sum formula."""
    mask = (y == 0) | (y == 1)
    p, y = p[mask], y[mask]
    pos, neg = int(y.sum()), int(len(y) - y.sum())
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
    # average ranks for ties
    sorted_p = p[order]
    i = 0
    while i < len(p):
        j = i
        while j + 1 < len(p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2
        i = j + 1
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def prob_metrics(p: np.ndarray, y: np.ndarray) -> dict:
    return {"log_loss": log_loss(p, y), "brier": brier(p, y), "ece": ece(p, y), "auc": auc(p, y), "n": int(len(y))}


def latency(net, n: int = 300, seed: int = 0) -> dict:
    """Measured wall-clock for (a) one network evaluation and (b) a full AI move decision on real mid-game states."""
    from rudhabot.ml.features import encoder_for
    from rudhabot.ml.policy import NetEvaluator, choose
    from rudhabot.ml.selfplay import deck_pair
    from rudhabot.tcg import engine as E
    from rudhabot.tcg.decks import ARCHETYPES
    encode_one = encoder_for(net.ARCH)[0]
    rng = random.Random(seed)
    states = []
    for s in range(40):
        a, b = deck_pair(10_000 + s)
        g = E.new_game([list(ARCHETYPES[a]["cards"]), list(ARCHETYPES[b]["cards"])], ["a", "b"], seed=s)
        for _ in range(rng.randrange(20, 90)):
            if g.over:
                break
            E.apply(g, rng.choice(E.legal_actions(g)).key)
        if not g.over:
            states.append(g)
    evals, moves, branching = [], [], []
    evaluator = NetEvaluator(net)
    for i in range(n):
        g = states[i % len(states)]
        arrays = encode_one(g, E.decider(g))
        t = time.perf_counter()
        net.predict(*[a[None] for a in arrays])
        evals.append(time.perf_counter() - t)
    for g in states:
        branching.append(len(E.legal_actions(g)))
        t = time.perf_counter()
        choose(g, evaluator, rng)
        moves.append(time.perf_counter() - t)
    ms = lambda xs, q: float(np.percentile(np.array(xs) * 1000, q))
    return {"eval_ms_p50": ms(evals, 50), "eval_ms_p95": ms(evals, 95), "move_ms_p50": ms(moves, 50),
            "move_ms_p95": ms(moves, 95), "mean_branching": float(np.mean(branching)), "states": len(states)}


def main() -> None:
    from rudhabot.ml.model_store import ModelStore
    from rudhabot.ml.selfplay import PolicySpec, arena
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default="models")
    parser.add_argument("--games", type=int, default=100, help="seeds per arena (each played twice, seats swapped)")
    args = parser.parse_args()
    store = ModelStore(args.models)
    path = store.current_path()
    net = store.load_current()
    seeds = range(5_000_000, 5_000_000 + args.games)
    report = {
        "version": net.meta.get("version"),
        "vs_heuristic": arena(PolicySpec("net", path), PolicySpec("heuristic"), seeds),
        "vs_random": arena(PolicySpec("net", path), PolicySpec("random"), seeds),
        "latency": latency(net),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

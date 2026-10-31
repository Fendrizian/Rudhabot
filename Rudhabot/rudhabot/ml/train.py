"""Reproducible training pipeline for the Cloudfall Oracle.

    python -m rudhabot.ml.train --version oracle-v1            # full build (a few minutes on 4 CPU cores)
    python -m rudhabot.ml.train --version smoke --games 60 --gens 1 --eval-games 10 --no-current   # quick check

Pipeline (all data is engine self-play; every step is seeded):
  gen 0  heuristic-search players (ε-greedy) play `games` games  -> train OracleNet
  gen k  the previous OracleNet plays itself and the heuristic     -> retrain on gen k + gen k-1 data (expert iteration)
  eval   held-out test games (disjoint seeds) for log-loss / Brier / ECE / AUC vs the heuristic baseline,
         seat-swapped arenas vs heuristic and random, latency, learned card values
  save   models/<version>.npz + manifest entry (schema hash, config, metrics)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import datetime, timezone

import numpy as np

from rudhabot.ml.evaluate import latency, prob_metrics
from rudhabot.ml.features import DENSE_DIM, N_CARDS, schema_hash
from rudhabot.ml.nn import OracleNet
from rudhabot.ml.oracle_store import ModelStore
from rudhabot.ml.selfplay import PolicySpec, arena, generate
from rudhabot.tcg.cards import CARD_IDS, CARD_BY_ID

TRAIN_SEED_BASE = 0
TEST_SEED_BASE = 9_000_000
CALIBRATION_SEED_BASE = 8_000_000
ARENA_SEED_BASE = 5_000_000


def _spec_mixer(primary: PolicySpec, rng_seed: int):
    """Most seats use `primary`; some use the heuristic or random play, for diversity of positions."""
    def specs(seed: int):
        r = random.Random(seed * 31 + rng_seed)
        out = []
        for _ in range(2):
            x = r.random()
            if x < 0.10:
                out.append(PolicySpec("random"))
            elif x < 0.35 and primary.kind == "net":
                out.append(PolicySpec("heuristic", epsilon=0.08, temperature=0.02))
            else:
                out.append(primary)
        return tuple(out)
    return specs


def card_values(net: OracleNet, dense: np.ndarray, bags: np.ndarray, sample: int = 2000, seed: int = 0) -> dict:
    """Learned value of each card: mean change in P(win) when one copy is added to the player's hand / board."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(dense), size=min(sample, len(dense)), replace=False)
    d, b = dense[idx], bags[idx]
    base = net.predict(d, b)
    values = {}
    for c, cid in enumerate(CARD_IDS):
        if CARD_BY_ID[cid].kind == "Sky":
            row = 1
        else:
            row = 0
        bb = b.copy()
        bb[:, row, c] += 1
        hand = float(np.mean(net.predict(d, bb) - base))
        bb = b.copy()
        bb[:, 1, c] += 1
        board = float(np.mean(net.predict(d, bb) - base))
        values[cid] = {"hand": round(hand, 5), "board": round(board, 5)}
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default="oracle-v1")
    parser.add_argument("--models", default="models")
    parser.add_argument("--games", type=int, default=4000, help="training games per generation")
    parser.add_argument("--test-games", type=int, default=500)
    parser.add_argument("--gens", type=int, default=3)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--record", type=float, default=0.12, help="fraction of decision points recorded")
    parser.add_argument("--eval-games", type=int, default=150, help="arena seeds (each played twice, seats swapped)")
    parser.add_argument("--processes", type=int, default=0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--no-current", action="store_true", help="register without making it the current version")
    args = parser.parse_args()

    t0 = time.time()
    store = ModelStore(args.models)
    tmp_path = os.path.join(args.models, f".{args.version}.gen.npz")
    os.makedirs(args.models, exist_ok=True)
    net = None
    datasets = []
    gen_log = []
    primary = PolicySpec("heuristic", epsilon=0.1, temperature=0.02)
    for gen in range(args.gens):
        print(f"[gen {gen}] self-play with {primary.kind} ({args.games} games)")
        seeds = range(TRAIN_SEED_BASE + gen * 100_000, TRAIN_SEED_BASE + gen * 100_000 + args.games)
        data, results = generate(seeds, _spec_mixer(primary, args.seed + gen), args.record, args.processes)
        datasets = (datasets + [data])[-2:]
        dense = np.concatenate([d[0] for d in datasets]); bags = np.concatenate([d[1] for d in datasets])
        labels = np.concatenate([d[2] for d in datasets])
        print(f"[gen {gen}] {len(labels):,} training states from {len(datasets)} generation(s); training")
        test_seeds = range(TEST_SEED_BASE + gen * 100_000, TEST_SEED_BASE + gen * 100_000 + args.test_games)
        test, _ = generate(test_seeds, _spec_mixer(primary, args.seed + 1000 + gen), args.record, args.processes)
        if net is None:
            net = OracleNet(DENSE_DIM, N_CARDS, seed=args.seed)
        history = net.fit(dense, bags, labels, epochs=args.epochs, seed=args.seed + gen, val=(test[0], test[1], test[2]))
        gen_log.append({"gen": gen, "policy": primary.kind, "train_states": int(len(labels)),
                        "test_states": int(len(test[2])), "history": history})
        net.meta = {"version": args.version, "schema": schema_hash()}
        net.save(tmp_path)
        primary = PolicySpec("net", tmp_path, epsilon=0.1, temperature=0.02)

    print("[calibrate] Platt scaling on separate calibration games")
    cal_seeds = range(CALIBRATION_SEED_BASE, CALIBRATION_SEED_BASE + args.test_games)
    cal, _ = generate(cal_seeds, _spec_mixer(primary, args.seed + 2000), args.record, args.processes)
    raw = prob_metrics(net.predict(test[0], test[1]), test[2])
    print("  calibration (a, b) =", net.calibrate(cal[0], cal[1], cal[2]))
    net.save(tmp_path)
    print("[eval] held-out probabilistic metrics")
    p_net = net.predict(test[0], test[1])
    metrics = {"oracle": prob_metrics(p_net, test[2]), "oracle_uncalibrated": raw, "heuristic_baseline": prob_metrics(test[4], test[2]),
               "constant_0.5": prob_metrics(np.full(len(test[2]), 0.5), test[2])}
    print(json.dumps(metrics, indent=2))
    print("[eval] arenas")
    arena_seeds = range(ARENA_SEED_BASE, ARENA_SEED_BASE + args.eval_games)
    strength = {"vs_heuristic": arena(PolicySpec("net", tmp_path), PolicySpec("heuristic"), arena_seeds, args.processes),
                "vs_random": arena(PolicySpec("net", tmp_path), PolicySpec("random"), arena_seeds, args.processes),
                "heuristic_vs_random": arena(PolicySpec("heuristic"), PolicySpec("random"), arena_seeds, args.processes)}
    print(json.dumps(strength, indent=2))
    lat = latency(net)
    print("[eval] latency", json.dumps(lat))
    values = card_values(net, test[0], test[1])
    info = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": net.n_params(), "calibration": list(net.calibration), "dense_dim": DENSE_DIM, "n_cards": N_CARDS,
        "config": {k: v for k, v in vars(args).items() if k not in ("models", "processes")},
        "data": "engine self-play only (no user data); train seeds and test seeds are disjoint",
        "generations": gen_log, "metrics": metrics, "strength": strength, "latency": lat,
        "build_seconds": round(time.time() - t0, 1), "card_values": values,
    }
    path = store.register(args.version, net, info, make_current=not args.no_current)
    os.remove(tmp_path)
    print(f"saved {path} ({os.path.getsize(path) / 1024:.0f} KB) in {info['build_seconds']} s")


if __name__ == "__main__":
    main()

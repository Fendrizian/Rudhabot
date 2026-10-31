"""Reproducible training pipeline for Clairvoyance, Rudhabot's Cloudfall model.

    python -m rudhabot.ml.train --version clairvoyance-v2              # full build (v2 slot network)
    python -m rudhabot.ml.train --version smoke --arch v2 --games 60 --test-games 20 --gens 1 --eval-games 10 --no-current

Pipeline (all data is engine self-play; every step is seeded):
  gen 0  heuristic-search players (ε-greedy) play `games` games              -> train on Monte-Carlo targets
  gen k  the previous network plays itself / the heuristic / random seats      -> retrain on gens k and k-1 with TD(λ)
         targets (expert iteration); 40% of decks are legal random mutations of the archetypes
  cal    Platt scaling on separate calibration games
  eval   held-out test games (disjoint seeds, scored on TRUE outcomes): log-loss / Brier / ECE / AUC vs the heuristic,
         seat-swapped arenas (net, blend, vs heuristic, vs random, vs the previously served model), latency,
         learned card values
  save   models/<version>.npz + manifest entry (architecture, schema hash, config, metrics)
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
from rudhabot.ml.features import DENSE_DIM, GLOB_DIM, N_CARDS, SLOT_FEATURES
from rudhabot.ml.nn import ClairvoyanceNet, ClairvoyanceSlotNet
from rudhabot.ml.model_store import ModelStore, _schema
from rudhabot.ml.selfplay import PolicySpec, RecordSpec, arena, generate
from rudhabot.tcg.cards import CARD_BY_ID, CARD_IDS

TRAIN_SEED_BASE = 0
TEST_SEED_BASE = 9_000_000
CALIBRATION_SEED_BASE = 8_000_000
ARENA_SEED_BASE = 5_500_000


def _spec_mixer(primary: PolicySpec, rng_seed: int):
    """Most seats use `primary`; some use the heuristic or random play, for diversity of positions."""
    def specs(seed: int):
        r = random.Random(seed * 31 + rng_seed)
        out = []
        for _ in range(2):
            x = r.random()
            if x < 0.10:
                out.append(PolicySpec("random"))
            elif x < 0.35 and primary.kind != "heuristic":
                out.append(PolicySpec("heuristic", epsilon=0.08, temperature=0.02))
            else:
                out.append(primary)
        return tuple(out)
    return specs


def new_net(arch: str, seed: int):
    if arch == "v1":
        return ClairvoyanceNet(DENSE_DIM, N_CARDS, seed=seed)
    return ClairvoyanceSlotNet(GLOB_DIM, SLOT_FEATURES, N_CARDS, seed=seed)


def card_values(net, inputs, sample: int = 2000, seed: int = 0) -> dict:
    """Learned value of each card: mean change in P(win) when one copy is added to the player's hand / board."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(inputs[0]), size=min(sample, len(inputs[0])), replace=False)
    sub = [x[idx] for x in inputs]
    bags_i = len(sub) - 1                                  # bags are the last input for every architecture
    base = net.predict(*sub)
    values = {}
    for c, cid in enumerate(CARD_IDS):
        out = {}
        for name, row in (("hand", 0), ("board", 1)):
            trial = list(sub)
            trial[bags_i] = sub[bags_i].copy()
            trial[bags_i][:, row, c] += 1
            out[name] = round(float(np.mean(net.predict(*trial) - base)), 5)
        values[cid] = out
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default="clairvoyance-v2")
    parser.add_argument("--arch", choices=("v1", "v2"), default="v2")
    parser.add_argument("--models", default="models")
    parser.add_argument("--games", type=int, default=6000, help="training games per generation")
    parser.add_argument("--test-games", type=int, default=600)
    parser.add_argument("--gens", type=int, default=3)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--record", type=float, default=0.12, help="fraction of decision points recorded")
    parser.add_argument("--td-lambda", type=float, default=0.7, help="λ for TD(λ) targets in generations >= 1")
    parser.add_argument("--mutate", type=float, default=0.4, help="probability each deck is a mutated archetype")
    parser.add_argument("--eval-games", type=int, default=200, help="arena seeds (each played twice, seats swapped)")
    parser.add_argument("--processes", type=int, default=0)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--no-current", action="store_true", help="register without making it the current version")
    args = parser.parse_args()

    t0 = time.time()
    store = ModelStore(args.models)
    previous = store.current_version()
    tmp_path = os.path.join(args.models, f".{args.version}.gen.npz")
    os.makedirs(args.models, exist_ok=True)
    net, datasets, gen_log = None, [], []
    primary = PolicySpec("heuristic", epsilon=0.1, temperature=0.02)
    for gen in range(args.gens):
        lam = 1.0 if primary.kind == "heuristic" else args.td_lambda
        rec = RecordSpec(arch=args.arch, rate=args.record, td_lambda=lam, mutate=args.mutate)
        print(f"[gen {gen}] self-play with {primary.kind} ({args.games} games, λ={lam})")
        seeds = range(TRAIN_SEED_BASE + gen * 100_000, TRAIN_SEED_BASE + gen * 100_000 + args.games)
        data, _ = generate(seeds, _spec_mixer(primary, args.seed + gen), rec, args.processes)
        datasets = (datasets + [data])[-2:]
        inputs = tuple(np.concatenate([d[0][i] for d in datasets]) for i in range(len(datasets[0][0])))
        targets = np.concatenate([d[1] for d in datasets])
        test_rec = RecordSpec(arch=args.arch, rate=args.record, td_lambda=1.0, mutate=args.mutate)
        test_seeds = range(TEST_SEED_BASE + gen * 100_000, TEST_SEED_BASE + gen * 100_000 + args.test_games)
        test, _ = generate(test_seeds, _spec_mixer(primary, args.seed + 1000 + gen), test_rec, args.processes)
        print(f"[gen {gen}] {len(targets):,} training states, {len(test[2]):,} test states; training")
        if net is None:
            net = new_net(args.arch, args.seed)
        history = net.fit(inputs, targets, epochs=args.epochs, seed=args.seed + gen, val=(test[0], test[2]))
        gen_log.append({"gen": gen, "policy": primary.kind, "td_lambda": lam, "train_states": int(len(targets)),
                        "test_states": int(len(test[2])), "history": history})
        net.meta = {"version": args.version, "schema": _schema(net.ARCH)}
        net.save(tmp_path)
        primary = PolicySpec("net", tmp_path, epsilon=0.1, temperature=0.02)

    print("[calibrate] Platt scaling on separate calibration games")
    cal_rec = RecordSpec(arch=args.arch, rate=args.record, td_lambda=1.0, mutate=args.mutate)
    cal, _ = generate(range(CALIBRATION_SEED_BASE, CALIBRATION_SEED_BASE + args.test_games),
                      _spec_mixer(primary, args.seed + 2000), cal_rec, args.processes)
    raw = prob_metrics(net.predict(*test[0]), test[2])
    print("  calibration (a, b) =", net.calibrate(cal[0], cal[2]))
    net.save(tmp_path)

    print("[eval] held-out probabilistic metrics (true outcomes)")
    metrics = {"model": prob_metrics(net.predict(*test[0]), test[2]), "model_uncalibrated": raw,
               "heuristic_baseline": prob_metrics(test[4], test[2]),
               "constant_0.5": prob_metrics(np.full(len(test[2]), 0.5), test[2])}
    print(json.dumps(metrics, indent=2))
    print("[eval] arenas")
    seeds = range(ARENA_SEED_BASE, ARENA_SEED_BASE + args.eval_games)
    blend = PolicySpec("blend", tmp_path, weight=0.5)
    strength = {
        "net_vs_heuristic": arena(PolicySpec("net", tmp_path), PolicySpec("heuristic"), seeds, args.processes),
        "blend_vs_heuristic": arena(blend, PolicySpec("heuristic"), seeds, args.processes),
        "blend_vs_heuristic_mutated_decks": arena(blend, PolicySpec("heuristic"), seeds, args.processes, mutate=1.0),
        "net_vs_random": arena(PolicySpec("net", tmp_path), PolicySpec("random"), seeds, args.processes),
    }
    if previous:
        try:
            prev_path = store.current_path(previous)
            strength[f"blend_vs_{previous}_blend"] = arena(blend, PolicySpec("blend", prev_path, weight=0.5), seeds,
                                                           args.processes)
        except Exception as error:                       # previous artifact missing: report, don't fail the build
            strength["previous_model_error"] = str(error)
    print(json.dumps(strength, indent=2))
    lat = latency(net)
    print("[eval] latency", json.dumps(lat))
    info = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": net.n_params(), "calibration": list(net.calibration),
        "config": {k: v for k, v in vars(args).items() if k not in ("models", "processes")},
        "data": "engine self-play only (no user data); train, calibration, test and arena seeds are disjoint; "
                "metrics use true game outcomes",
        "generations": gen_log, "metrics": metrics, "strength": strength, "latency": lat,
        "build_seconds": round(time.time() - t0, 1), "card_values": card_values(net, test[0]),
        "serving": {"move_policy": "blend: logit(P_net) + 0.5 * logit(P_heuristic), 1-ply determinized afterstate search",
                    "probabilities": "calibrated network only"},
    }
    path = store.register(args.version, net, info, make_current=not args.no_current)
    os.remove(tmp_path)
    print(f"saved {path} ({os.path.getsize(path) / 1024:.0f} KB) in {info['build_seconds']} s")


if __name__ == "__main__":
    main()

"""Reproducible training pipeline for Clairvoyance, Rudhabot's Cloudfall model.

    python -m rudhabot.ml.train --version clairvoyance-v3 --init clairvoyance-v2      # the v3 build (warm start)
    python -m rudhabot.ml.train --version clairvoyance-v2                              # v2 recipe from scratch
    python -m rudhabot.ml.train --version smoke --games 60 --test-games 20 --gens 1 --eval-games 10 --no-current

Pipeline (all data is engine self-play; every step is seeded):
  gen 0  data from the warm-start model's served policy (--init), or from heuristic-search players (ε-greedy)
         -> train (Monte-Carlo targets for heuristic data, TD(λ) for model data)
  gen k  the newest network's policy plays (mixed with heuristic and random seats) -> retrain on gens k and k-1 with
         TD(λ) targets (expert iteration); 40% of decks are legal random mutations of the archetypes
         training options: EMA weight averaging (--ema), hand-hidden augmentation for the spectator win bar
         (--hide-hand), early stopping on held-out games
  cal    Platt scaling on separate calibration games
  eval   held-out test games (disjoint seeds, TRUE outcomes): log-loss / Brier / ECE / AUC vs the heuristic;
         the serving blend weight is chosen from --blend-weights by seat-swapped arenas; arenas vs the heuristic,
         random and the previously served model; latency; learned card values
  save   models/<version>.npz + manifest entry (architecture, schema hash, config, metrics, serving.blend_weight)
For an apples-to-apples comparison with earlier versions on fresh seeds, run `python -m rudhabot.ml.benchmark`.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import datetime, timezone

from rudhabot.ml import single_threaded_blas

single_threaded_blas()

import numpy as np  # noqa: E402  (after the BLAS thread setting)

from rudhabot.ml.evaluate import latency, prob_metrics
from rudhabot.ml.features import DENSE_DIM, GLOB_DIM, N_CARDS, SLOT_FEATURES
from rudhabot.ml.nn import ClairvoyanceNet, ClairvoyanceSlotNet, load_net
from rudhabot.ml.model_store import ModelStore, _schema
from rudhabot.ml.selfplay import PolicySpec, RecordSpec, arena, generate
from rudhabot.tcg.cards import CARD_BY_ID, CARD_IDS

TRAIN_SEED_BASE = 0
TEST_SEED_BASE = 9_000_000
CALIBRATION_SEED_BASE = 8_000_000
ARENA_SEED_BASE = 5_500_000


def hide_hand_augment(fraction: float):
    """Training-batch augmentation: zero the own-hand bag on `fraction` of rows. The spectator win bar hides both
    hands, so the network should also be good at predicting without seeing one (the label is unchanged)."""
    def augment(batch, rng):
        bags = batch[-1].copy()                          # bags are the last input for every architecture
        bags[rng.random(len(bags)) < fraction, 0, :] = 0
        return [*batch[:-1], bags]
    return augment


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


def choose_blend_weight(path: str, weights, seeds, processes: int, previous_spec=None, log=print) -> tuple[float, dict]:
    """Serving blend weight w (logit(P_net) + w * logit(P_heuristic)) with the best mean arena score vs the heuristic
    and, if there is one, the previously served model. Chosen on training arena seeds; confirm on fresh seeds with
    the benchmark."""
    results, best = {}, None
    for w in weights:
        spec = PolicySpec("blend", path, weight=w) if w > 0 else PolicySpec("net", path)
        row = {"vs_heuristic": arena(spec, PolicySpec("heuristic"), seeds, processes)}
        if previous_spec is not None:
            row["vs_previous"] = arena(spec, previous_spec, seeds, processes)
        row["mean_score"] = float(np.mean([r["score"] for r in row.values()]))
        results[str(w)] = row
        log(f"  blend weight {w}: " + ", ".join(f"{k} {v['score']:.3f}" for k, v in row.items() if k != "mean_score"))
        if best is None or row["mean_score"] > results[str(best)]["mean_score"]:
            best = w
    return best, results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default="clairvoyance-v3")
    parser.add_argument("--arch", choices=("v1", "v2"), default="v2")
    parser.add_argument("--init", default="", help="warm-start from this registered version (same architecture)")
    parser.add_argument("--models", default="models")
    parser.add_argument("--games", type=int, default=6000, help="training games per generation")
    parser.add_argument("--test-games", type=int, default=600)
    parser.add_argument("--gens", type=int, default=3)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--l2", type=float, default=1e-4)
    parser.add_argument("--ema", type=float, default=0.0, help="Polyak weight averaging decay (0 = off), e.g. 0.999")
    parser.add_argument("--hide-hand", type=float, default=0.0, help="fraction of training rows seen without the hand")
    parser.add_argument("--record", type=float, default=0.12, help="fraction of decision points recorded")
    parser.add_argument("--td-lambda", type=float, default=0.7, help="λ for TD(λ) targets on model-played data")
    parser.add_argument("--mutate", type=float, default=0.4, help="probability each deck is a mutated archetype")
    parser.add_argument("--eval-games", type=int, default=200, help="arena seeds (each played twice, seats swapped)")
    parser.add_argument("--blend-weights", default="0.5", help="comma-separated serving blend weights to compare")
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
    augment = hide_hand_augment(args.hide_hand) if args.hide_hand > 0 else None
    if args.init:
        init_path = store.current_path(args.init)
        net = load_net(init_path)
        if net.ARCH != args.arch:
            raise SystemExit(f"--init {args.init} is a {net.ARCH} network, not {args.arch}")
        net.calibration = (1.0, 0.0)                    # recalibrated at the end
        init_weight = store.manifest()["versions"][args.init].get("serving", {}).get("blend_weight", 0.5)
        primary = PolicySpec("blend", init_path, epsilon=0.1, temperature=0.02, weight=init_weight)
        print(f"[init] warm start from {args.init} ({net.n_params():,} params); its served policy plays gen 0")
    else:
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
        history = net.fit(inputs, targets, epochs=args.epochs, lr=args.lr, l2=args.l2, seed=args.seed + gen,
                          val=(test[0], test[2]), ema=args.ema, augment=augment)
        gen_log.append({"gen": gen, "policy": primary.kind, "td_lambda": lam, "train_states": int(len(targets)),
                        "test_states": int(len(test[2])), "history": history})
        net.meta = {"version": args.version, "schema": _schema(net.ARCH)}
        net.save(tmp_path)
        primary = PolicySpec("blend", tmp_path, epsilon=0.1, temperature=0.02, weight=0.5) if args.init else \
            PolicySpec("net", tmp_path, epsilon=0.1, temperature=0.02)

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
    seeds = range(ARENA_SEED_BASE, ARENA_SEED_BASE + args.eval_games)
    previous_spec = None
    if previous:
        try:
            prev_weight = store.manifest()["versions"][previous].get("serving", {}).get("blend_weight", 0.5)
            previous_spec = PolicySpec("blend", store.current_path(previous), weight=prev_weight)
        except Exception as error:                       # previous artifact missing: report, don't fail the build
            print(f"  previous model unavailable: {error}")
    print("[eval] serving blend weight")
    weights = [float(w) for w in args.blend_weights.split(",") if w.strip()]
    blend_weight, blend_search = choose_blend_weight(tmp_path, weights, seeds, args.processes, previous_spec)
    print(f"  chosen blend weight: {blend_weight}")
    print("[eval] arenas")
    served = PolicySpec("blend", tmp_path, weight=blend_weight) if blend_weight > 0 else PolicySpec("net", tmp_path)
    strength = {
        "net_vs_heuristic": arena(PolicySpec("net", tmp_path), PolicySpec("heuristic"), seeds, args.processes),
        "blend_vs_heuristic": arena(served, PolicySpec("heuristic"), seeds, args.processes),
        "blend_vs_heuristic_mutated_decks": arena(served, PolicySpec("heuristic"), seeds, args.processes, mutate=1.0),
        "net_vs_random": arena(PolicySpec("net", tmp_path), PolicySpec("random"), seeds, args.processes),
    }
    if previous_spec is not None:
        strength[f"blend_vs_{previous}_blend"] = arena(served, previous_spec, seeds, args.processes)
    print(json.dumps(strength, indent=2))
    lat = latency(net, n=2000)
    print("[eval] latency", json.dumps(lat))
    info = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": net.n_params(), "calibration": list(net.calibration),
        "config": {k: v for k, v in vars(args).items() if k not in ("models", "processes")},
        "data": "engine self-play only (no user data); train, calibration, test and arena seeds are disjoint; "
                "metrics use true game outcomes",
        "generations": gen_log, "metrics": metrics, "strength": strength, "blend_search": blend_search,
        "latency": lat, "build_seconds": round(time.time() - t0, 1), "card_values": card_values(net, test[0]),
        "serving": {"move_policy": f"blend: logit(P_net) + {blend_weight:g} * logit(P_heuristic), 1-ply determinized "
                                   "afterstate search", "blend_weight": blend_weight,
                    "probabilities": "calibrated network only"},
    }
    path = store.register(args.version, net, info, make_current=not args.no_current)
    os.remove(tmp_path)
    print(f"saved {path} ({os.path.getsize(path) / 1024:.0f} KB) in {info['build_seconds']} s")


if __name__ == "__main__":
    main()

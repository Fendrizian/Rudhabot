"""Refit a model's Platt calibration on a broad mixture of positions (fresh seeds), without retraining it.

    python -m rudhabot.ml.recalibrate --version clairvoyance-v3

A network calibrated on games from one policy can be over- or under-confident on another. This fits (a, b) on:
  * games played by heuristic-search players (ε-greedy) and by the model's own served policy, half archetype decks
    and 40% mutated, from calibration seeds no benchmark or training run uses (8,100,000+);
  * the same states with the own hand hidden (the spectator win bar), at a third of the weight.
The test seeds of `python -m rudhabot.ml.benchmark` are never touched, so the benchmark stays an honest check.

The AI blends the calibrated network with the heuristic: logit(P_net) + w * logit(P_heuristic). Platt scaling
multiplies the network's logit by a, so the stored blend weight is rescaled by a_new / a_old: the AI's move choices
stay exactly the ones that were measured (only the probabilities shown to people change).
"""
from __future__ import annotations

import argparse
import json
import os

from rudhabot.ml import single_threaded_blas

single_threaded_blas()

import numpy as np  # noqa: E402

from rudhabot.ml.evaluate import prob_metrics  # noqa: E402
from rudhabot.ml.model_store import ModelStore  # noqa: E402
from rudhabot.ml.nn import load_net  # noqa: E402
from rudhabot.ml.selfplay import PolicySpec, RecordSpec, generate  # noqa: E402
from rudhabot.services.clairvoyance import LOOKAHEAD_TOP  # noqa: E402

CAL_SEEDS = 8_100_000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True)
    parser.add_argument("--models", default="models")
    parser.add_argument("--games", type=int, default=800, help="games per policy")
    parser.add_argument("--processes", type=int, default=0)
    args = parser.parse_args()
    store = ModelStore(args.models)
    manifest = store.manifest()
    entry = manifest["versions"][args.version]
    path = os.path.join(store.directory, entry["file"])
    net = load_net(path)
    serving = entry.setdefault("serving", {})
    weight = serving.get("blend_weight", 0.5)
    look = int(serving.get("lookahead_top", LOOKAHEAD_TOP))
    served = PolicySpec("blend", path, epsilon=0.05, temperature=0.02, weight=weight, look=look)
    heuristic = PolicySpec("heuristic", epsilon=0.1, temperature=0.02)
    rec = RecordSpec(arch=net.ARCH, rate=0.12, td_lambda=1.0, mutate=0.4)
    parts = []
    for i, spec in enumerate((heuristic, served)):
        seeds = range(CAL_SEEDS + i * 100_000, CAL_SEEDS + i * 100_000 + args.games)
        data, _ = generate(seeds, lambda s, spec=spec: (spec, spec), rec, args.processes)
        parts.append(data)
    inputs = tuple(np.concatenate([p[0][k] for p in parts]) for k in range(len(parts[0][0])))
    y = np.concatenate([p[2] for p in parts])
    rng = np.random.default_rng(0)
    pick = rng.random(len(y)) < 1 / 3
    hidden = [x[pick] for x in inputs]
    hidden[-1] = hidden[-1].copy()
    hidden[-1][:, 0, :] = 0
    cal_inputs = tuple(np.concatenate([a, b]) for a, b in zip(inputs, hidden))
    cal_y = np.concatenate([y, y[pick]])
    before = {"calibration": list(net.calibration), "on_this_set": prob_metrics(net.predict(*cal_inputs), cal_y)}
    a_old = float(net.calibration[0])
    a, b = net.calibrate(cal_inputs, cal_y)
    after = {"calibration": [a, b], "on_this_set": prob_metrics(net.predict(*cal_inputs), cal_y)}
    net.save(path)
    entry["calibration"] = [a, b]
    if weight > 0 and a_old > 0:
        serving["blend_weight"] = round(weight * a / a_old, 4)      # identical move choices (see the docstring)
    entry["recalibration"] = {"seeds": [CAL_SEEDS, CAL_SEEDS + 100_000 + args.games - 1], "games_per_policy": args.games,
                              "states": int(len(cal_y)),
                              "policies": ["heuristic (eps 0.1)", f"served blend w={weight}, lookahead top {look}"],
                              "hand_hidden_fraction": 1 / 3, "before": before, "after": after,
                              "blend_weight": {"before": weight, "after": serving.get("blend_weight", weight),
                                               "why": "rescaled by a_new / a_old so move choices are unchanged"}}
    store._write(manifest)
    print(json.dumps(entry["recalibration"], indent=2))


if __name__ == "__main__":
    main()

"""Apples-to-apples benchmark for Clairvoyance's Cloudfall value networks.

    python -m rudhabot.ml.benchmark                                   # every version in models/manifest.json
    python -m rudhabot.ml.benchmark --versions oracle-v1,clairvoyance-v2 --games 500 --arena-seeds 300

Every model is scored on the *same* held-out states (seeds disjoint from all training, calibration, test and arena
seeds), against the *true* game outcome:

  probabilities   log-loss, Brier, ECE, AUC for the full view (what the AI and the coach use) and the public view
                  (own hand hidden: the spectator win bar), split by deck type (archetype / mutated) and game stage
                  (rounds 1-3 / 4-7 / 8+), on two state sources: heuristic-played games and model-played games.
  paired CIs      for each pair of models, the difference in log-loss / Brier / AUC with a 95% interval from a
                  bootstrap that resamples whole games (states of one game are correlated, so rows are not resampled).
  strength        seat-swapped arenas (Wilson 95% intervals): each model's served policy vs the heuristic and vs
                  random, and every pair of models head-to-head.
  latency         one evaluation and one full AI move (p50 / p95), single thread.

Writes a JSON report and a Markdown summary.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import time
from datetime import datetime, timezone

from rudhabot.ml import single_threaded_blas

single_threaded_blas()

import numpy as np  # noqa: E402  (after the BLAS thread setting)

from rudhabot.ml.evaluate import latency, prob_metrics  # noqa: E402
from rudhabot.ml.features import encoder_for  # noqa: E402
from rudhabot.ml.model_store import ModelStore  # noqa: E402
from rudhabot.ml.nn import load_net  # noqa: E402
from rudhabot.ml.policy import HeuristicEvaluator  # noqa: E402
from rudhabot.ml.selfplay import PolicySpec, arena, build_policy, game_decks  # noqa: E402
from rudhabot.tcg import engine as E  # noqa: E402

HEURISTIC_PLAY_SEEDS = 7_700_000          # the same seeds as the v1-vs-v2 benchmark, for continuity
MODEL_PLAY_SEEDS = 7_900_000
ARENA_SEEDS = 7_800_000
STAGES = (("rounds_1_3", 1, 3), ("rounds_4_7", 4, 7), ("rounds_8_plus", 8, 10_000))

_NETS: dict = {}


def _net(path: str):
    if path not in _NETS:
        _NETS[path] = load_net(path)
    return _NETS[path]


def _predict(net, g, seat: int, hide_hand: bool) -> float:
    arrays = list(encoder_for(net.ARCH)[0](g, seat))
    if hide_hand:
        arrays[-1] = arrays[-1].copy()
        arrays[-1][0, :] = 0                       # bags are last for every architecture; row 0 is the own hand
    return float(net.predict(*[a[None] for a in arrays])[0])


def _play(job):
    """One game; returns a row per sampled (state, perspective) with every model's predictions."""
    seed, player_spec, models, rate = job
    rng = random.Random(seed)
    mutate = 0.0 if seed % 2 == 0 else 1.0        # half archetype decks, half mutated decks
    g = E.new_game(game_decks(seed, mutate), ["a", "b"], seed=seed)
    policy = build_policy(player_spec)
    heuristic = HeuristicEvaluator()
    rows, steps = [], 0
    while not g.over and steps < 4000:
        acts = E.legal_actions(g)
        if len(acts) > 1 and rng.random() < rate:
            for seat in (0, 1):
                row = {"game": seed, "seat": seat, "mutated": bool(mutate), "round": g.round,
                       "heuristic": heuristic._value(g, seat)}
                for name, path in models.items():
                    net = _net(path)
                    row[name] = _predict(net, g, seat, False)
                    row[f"{name}@public"] = _predict(net, g, seat, True)
                rows.append(row)
        E.apply(g, (acts[0] if len(acts) == 1 else policy.pick(g, rng)).key)
        steps += 1
    for row in rows:
        row["y"] = 0.5 if g.winner in (-1, None) else float(g.winner == row["seat"])
    return rows


def collect(seeds, player_spec: PolicySpec, models: dict, rate: float, processes: int) -> list[dict]:
    jobs = [(s, player_spec, models, rate) for s in seeds]
    with mp.get_context("fork").Pool(processes) as pool:
        return [row for rows in pool.imap(_play, jobs, chunksize=4) for row in rows]


def _subsets(rows):
    yield "all", rows
    yield "archetype_decks", [r for r in rows if not r["mutated"]]
    yield "mutated_decks", [r for r in rows if r["mutated"]]
    for name, lo, hi in STAGES:
        yield name, [r for r in rows if lo <= r["round"] <= hi]


def table(rows, names) -> dict:
    out = {}
    for subset, sel in _subsets(rows):
        if not sel:
            continue
        y = np.array([r["y"] for r in sel])
        out[subset] = {"states": len(sel), "games": len({r["game"] for r in sel}),
                       "heuristic": prob_metrics(np.array([r["heuristic"] for r in sel]), y)}
        for name in names:
            out[subset][name] = prob_metrics(np.array([r[name] for r in sel]), y)
            out[subset][f"{name}@public"] = prob_metrics(np.array([r[f"{name}@public"] for r in sel]), y)
    return out


def paired_differences(rows, a: str, b: str, resamples: int = 1000, seed: int = 0) -> dict:
    """metric(a) - metric(b) with a 95% game-level bootstrap interval. Negative log-loss/Brier = a is better."""
    games = sorted({r["game"] for r in rows})
    by_game = {gid: [] for gid in games}
    for r in rows:
        by_game[r["game"]].append(r)
    packed = {gid: (np.array([r[a] for r in rs]), np.array([r[b] for r in rs]), np.array([r["y"] for r in rs]))
              for gid, rs in by_game.items()}
    rng = np.random.default_rng(seed)

    def metrics(gids):
        pa = np.concatenate([packed[g][0] for g in gids])
        pb = np.concatenate([packed[g][1] for g in gids])
        y = np.concatenate([packed[g][2] for g in gids])
        ma, mb = prob_metrics(pa, y), prob_metrics(pb, y)
        return {k: ma[k] - mb[k] for k in ("log_loss", "brier", "auc")}

    point = metrics(games)
    draws = {k: [] for k in point}
    for _ in range(resamples):
        sample = rng.choice(len(games), size=len(games), replace=True)
        d = metrics([games[i] for i in sample])
        for k, v in d.items():
            draws[k].append(v)
    return {k: {"diff": point[k], "ci95": [float(np.percentile(draws[k], 2.5)), float(np.percentile(draws[k], 97.5))]}
            for k in point}


def serving_spec(store: ModelStore, version: str) -> PolicySpec:
    """The move policy each version is served with (blend weight from its manifest entry, default 0.5)."""
    entry = store.manifest()["versions"][version]
    weight = entry.get("serving", {}).get("blend_weight", 0.5)
    return PolicySpec("blend", os.path.join(store.directory, entry["file"]), weight=weight)


def markdown(report: dict) -> str:
    names = report["versions"]
    lines = [f"# Clairvoyance benchmark ({report['created']})", "",
             "Every model is scored on the same held-out states against the true game outcome. "
             "Lower log-loss / Brier / ECE is better; higher AUC is better. *Public* = own hand hidden "
             "(the spectator win bar).", ""]
    for source, label in (("heuristic_play", "States from heuristic-played games"),
                          ("model_play", "States from model-played games")):
        tab = report["probabilities"].get(source)
        if not tab:
            continue
        lines += [f"## {label}", "", "| Subset | Model | log-loss | Brier | ECE | AUC | public log-loss | public AUC |",
                  "|---|---|---|---|---|---|---|---|"]
        for subset, cols in tab.items():
            lines.append(f"| {subset} ({cols['states']:,} states, {cols['games']} games) | heuristic | "
                         f"{cols['heuristic']['log_loss']:.3f} | {cols['heuristic']['brier']:.3f} | "
                         f"{cols['heuristic']['ece']:.3f} | {cols['heuristic']['auc']:.3f} | | |")
            for name in names:
                m, pub = cols[name], cols[f"{name}@public"]
                lines.append(f"| | {name} | {m['log_loss']:.3f} | {m['brier']:.3f} | {m['ece']:.3f} | {m['auc']:.3f} | "
                             f"{pub['log_loss']:.3f} | {pub['auc']:.3f} |")
        lines.append("")
    if report.get("paired"):
        lines += ["## Paired differences (A − B, 95% game-level bootstrap)", "",
                  "| A vs B | source | Δ log-loss | Δ Brier | Δ AUC |", "|---|---|---|---|---|"]
        for key, d in report["paired"].items():
            fmt = lambda m: f"{d[m]['diff']:+.4f} [{d[m]['ci95'][0]:+.4f}, {d[m]['ci95'][1]:+.4f}]"
            lines.append(f"| {key.split('|')[0]} | {key.split('|')[1]} | {fmt('log_loss')} | {fmt('brier')} | {fmt('auc')} |")
        lines.append("")
    lines += ["## Playing strength (seat-swapped arenas, Wilson 95%)", "", "| Matchup | Score | 95% CI | Games |",
              "|---|---|---|---|"]
    for key, a in report["strength"].items():
        lines.append(f"| {key} | {a['score'] * 100:.1f}% | {a['ci95'][0] * 100:.1f}–{a['ci95'][1] * 100:.1f} | {a['games']} |")
    lines += ["", "## Latency (single thread)", "", "| Model | eval p50 | eval p95 | move p50 | move p95 |", "|---|---|---|---|---|"]
    for name, lat in report["latency"].items():
        lines.append(f"| {name} | {lat['eval_ms_p50']:.3f} ms | {lat['eval_ms_p95']:.3f} ms | {lat['move_ms_p50']:.2f} ms | "
                     f"{lat['move_ms_p95']:.2f} ms |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default="models")
    parser.add_argument("--versions", default="", help="comma-separated; default: every version in the manifest")
    parser.add_argument("--games", type=int, default=500, help="games per state source")
    parser.add_argument("--rate", type=float, default=0.15, help="fraction of decision points sampled")
    parser.add_argument("--arena-seeds", type=int, default=300, help="each played twice, seats swapped")
    parser.add_argument("--processes", type=int, default=0)
    parser.add_argument("--out", default="models/benchmark")
    args = parser.parse_args()
    t0 = time.time()
    store = ModelStore(args.models)
    manifest = store.manifest()
    names = [v for v in (args.versions.split(",") if args.versions else manifest["versions"]) if v]
    paths = {v: os.path.join(store.directory, manifest["versions"][v]["file"]) for v in names}
    procs = args.processes or max(1, mp.cpu_count())
    current = manifest.get("current")
    player = serving_spec(store, current) if current in names else PolicySpec("heuristic", epsilon=0.1, temperature=0.02)

    print(f"[states] heuristic-played games ({args.games})")
    heur_rows = collect(range(HEURISTIC_PLAY_SEEDS, HEURISTIC_PLAY_SEEDS + args.games),
                        PolicySpec("heuristic", epsilon=0.1, temperature=0.02), paths, args.rate, procs)
    print(f"[states] model-played games ({args.games}, policy {os.path.basename(player.model_path or player.kind)})")
    model_rows = collect(range(MODEL_PLAY_SEEDS, MODEL_PLAY_SEEDS + args.games),
                         PolicySpec(player.kind, player.model_path, epsilon=0.05, temperature=0.02, weight=player.weight),
                         paths, args.rate, procs)
    report = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "versions": names,
              "seeds": {"heuristic_play": HEURISTIC_PLAY_SEEDS, "model_play": MODEL_PLAY_SEEDS, "arenas": ARENA_SEEDS},
              "probabilities": {"heuristic_play": table(heur_rows, names), "model_play": table(model_rows, names)},
              "paired": {}, "strength": {}, "latency": {}}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for source, rows in (("heuristic_play", heur_rows), ("model_play", model_rows)):
                report["paired"][f"{a} vs {b}|{source}"] = paired_differences(rows, a, b)
            report["paired"][f"{a}@public vs {b}@public|heuristic_play"] = paired_differences(heur_rows, f"{a}@public",
                                                                                             f"{b}@public")
    print("[strength] arenas")
    seeds = range(ARENA_SEEDS, ARENA_SEEDS + args.arena_seeds)
    specs = {v: serving_spec(store, v) for v in names}
    for v in names:
        report["strength"][f"{v} vs heuristic"] = arena(specs[v], PolicySpec("heuristic"), seeds, procs)
        report["strength"][f"{v} vs heuristic (mutated decks)"] = arena(specs[v], PolicySpec("heuristic"), seeds, procs, mutate=1.0)
        report["strength"][f"{v} vs random"] = arena(specs[v], PolicySpec("random"), seeds, procs)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            report["strength"][f"{a} vs {b}"] = arena(specs[a], specs[b], seeds, procs)
    print("[latency]")
    for v in names:
        report["latency"][v] = latency(_net(paths[v]), n=2000)
    report["seconds"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out + ".json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    with open(args.out + ".md", "w", encoding="utf-8") as handle:
        handle.write(markdown(report))
    print(markdown(report))
    print(f"wrote {args.out}.json and {args.out}.md in {report['seconds']} s")


if __name__ == "__main__":
    main()

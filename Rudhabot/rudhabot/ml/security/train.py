"""Train, evaluate and register the security models.

    python -m rudhabot.ml.security.train --data <folder for the public datasets> [--download]

1. Message-risk model: public datasets + synthetic server chat (datasets.py), feature hashing (hashing.py), the
   8.4M-parameter hashed multi-task network (sparse.py); Platt calibration on validation; test metrics with 95%
   confidence intervals, per source; false-positive rates on held-out synthetic chat; comparison with the rules alone.
2. Wargame: simulated servers (sim.py) replayed through the real engine (wargame.py). Training servers see 6 attack
   families; test servers see all 8 (slow_drip and malicious_bot are held out).
3. Incident model trained on the training servers' per-minute features; evaluated per minute and per attack.
4. Alert ranker trained on the training servers' alerts; evaluated on the test servers' alerts.
5. Full-system benchmark on the test servers: rules only vs rules + models.
Writes models/security/{message-risk-v1.npz, incident-v1.npz, ranker-v1.json, manifest.json, benchmark.json,
BENCHMARK.md}.
"""
from __future__ import annotations

from rudhabot.ml import single_threaded_blas

single_threaded_blas()

import argparse                                        # noqa: E402
import json                                            # noqa: E402
import os                                              # noqa: E402
import random                                          # noqa: E402
import time                                            # noqa: E402
from datetime import datetime, timezone                # noqa: E402
from multiprocessing import get_context                # noqa: E402

import numpy as np                                     # noqa: E402

from rudhabot.ml.security import datasets as D         # noqa: E402
from rudhabot.ml.security.hashing import SCHEMA as HASH_SCHEMA, encode, message_tokens, url_only_tokens  # noqa: E402
from rudhabot.ml.security.incident_model import (AlertRanker, IncidentNet, at_threshold, auc,  # noqa: E402
                                                 average_precision, ece)
from rudhabot.ml.security.serving import DEFAULT_DIR, SIGNAL_AT  # noqa: E402
from rudhabot.ml.security.sim import FAMILIES, HELD_OUT  # noqa: E402
from rudhabot.ml.security.sparse import HEADS, HashedMultiTaskNet, fit_platt  # noqa: E402
from rudhabot.ml.security.wargame import RANK_FEATURES, run_server, summarise  # noqa: E402
from rudhabot.security.features import DIM, SCHEMA as FEATURE_SCHEMA  # noqa: E402
from rudhabot.security.links import analyse_text  # noqa: E402
from rudhabot.security.text import _CONFUSABLE  # noqa: E402

HOMOGLYPHS = {}
for k, v in _CONFUSABLE.items():
    if len(v) == 1:
        HOMOGLYPHS.setdefault(v, k)
LEET = {"o": "0", "i": "1", "e": "3", "a": "4", "s": "5", "t": "7"}


# ============================================================================================ augmentation
def augment(text: str, rng: random.Random) -> str:
    """Obfuscations scammers use: look-alike letters, leetspeak, invisible characters, spacing, casing, new domains."""
    out = []
    for ch in text:
        r = rng.random()
        if ch.lower() in HOMOGLYPHS and r < 0.08:
            out.append(HOMOGLYPHS[ch.lower()])
        elif ch.lower() in LEET and r < 0.12:
            out.append(LEET[ch.lower()])
        else:
            out.append(ch)
        if ch.isalpha() and rng.random() < 0.02:
            out.append("​")
    text = "".join(out)
    if rng.random() < 0.3:
        text = text.upper() if rng.random() < 0.5 else text.lower()
    if rng.random() < 0.3:
        from rudhabot.ml.security.sim import _lookalike
        import re
        text = re.sub(r"https?://\S+", lambda m: _lookalike(rng), text) if "http" in text else text + " " + _lookalike(rng)
    return text


# ============================================================================================ arrays
def build(examples, rng: random.Random, augment_positive: int = 0, augment_negative: int = 0):
    rows = []
    for ex in examples:
        rows.append(ex)
        if ex.kind == "message" and augment_positive and (ex.labels.get("scam") == 1 or ex.labels.get("spam") == 1):
            rows += [D.Example(augment(ex.text, rng), "message", ex.labels, ex.source + "+aug", ex.group, ex.split)
                     for _ in range(augment_positive)]
        elif ex.kind == "message" and augment_negative and rng.random() < 0.5:
            rows += [D.Example(augment(ex.text, rng), "message", ex.labels, ex.source + "+aug", ex.group, ex.split)
                     for _ in range(augment_negative)]
    tokens = [message_tokens(r.text) if r.kind == "message" else url_only_tokens(r.text) for r in rows]
    idx, off = encode(tokens)
    y = np.zeros((len(rows), len(HEADS)), np.float32)
    mask = np.zeros((len(rows), len(HEADS)), np.float32)
    for i, r in enumerate(rows):
        for j, head in enumerate(HEADS):
            if head in r.labels:
                y[i, j] = r.labels[head]
                mask[i, j] = 1.0
    # balance classes within each head; URL examples (the bulk) count half
    for j in range(len(HEADS)):
        labelled = mask[:, j] > 0
        pos = float((y[labelled, j] == 1).sum())
        neg = float((y[labelled, j] == 0).sum())
        if pos and neg:
            mask[labelled & (y[:, j] == 1), j] = 0.5 * (pos + neg) / pos
            mask[labelled & (y[:, j] == 0), j] = 0.5 * (pos + neg) / neg
    weights = np.array([0.5 if r.kind == "url" else 1.0 for r in rows], np.float32)
    return (idx, off, y, mask, weights), rows


def evaluate_heads(net, data, rows) -> dict:
    idx, off, y, mask, _ = data
    p = net.predict(idx, off)
    out = {}
    for j, head in enumerate(HEADS):
        m = mask[:, j] > 0
        if not m.any():
            continue
        yy, pp = y[m, j], p[m, j]
        out[head] = {"n": int(m.sum()), "positives": int(yy.sum()), "auc": auc(yy, pp),
                     "average_precision": average_precision(yy, pp), "ece": round(ece(yy, pp), 4),
                     "brier": round(float(np.mean((pp - yy) ** 2)), 4),
                     "at_0.5": at_threshold(yy, pp, 0.5), f"at_{SIGNAL_AT}": at_threshold(yy, pp, SIGNAL_AT),
                     "at_0.97": at_threshold(yy, pp, 0.97)}
    return out, p


# ============================================================================================ wargame workers
def _server(job):
    seed, days, families, members, with_models, model_dir = job
    incident_model = message_model = None
    if with_models:
        from rudhabot.ml.security.serving import SecurityModels
        message_model, incident_model = SecurityModels(model_dir).load().scorers()
    results = run_server(seed, days, families=families, members=members, incident_model=incident_model,
                         message_model=message_model, collect=not with_models)
    X = np.asarray([v for r in results for v in r.features], np.float32) if not with_models else None
    labels = [l for r in results for l in r.labels] if not with_models else None
    alerts = [(v, t) for r in results for v, t in r.alerts]
    ranked = [x for r in results for x in r.ranked]
    flags = [f for r in results for f in r.model_flags]
    return {"seed": seed, "X": X, "labels": labels, "alerts": alerts, "ranked": ranked, "summary": summarise(results),
            "flags": flags,
            "results": [{"attacks": r.attacks, "benign": dict(r.benign)} for r in results]}


def _pool_map(jobs, workers):
    if workers <= 1:
        return [_server(j) for j in jobs]
    with get_context("fork").Pool(workers) as pool:
        return pool.map(_server, jobs)


def merge_summaries(parts: list[dict]) -> dict:
    from collections import defaultdict
    from rudhabot.ml.security.wargame import DayResult
    results = []
    for p in parts:
        for r in p["results"]:
            d = DayResult()
            d.attacks = r["attacks"]
            d.benign = defaultdict(int, r["benign"])
            results.append(d)
    return summarise(results)


def episodes(flags, threshold: float) -> tuple[int, int, dict]:
    """Group per-minute model readings into episodes (consecutive minutes above threshold): returns
    (false episodes outside attacks, total quiet minutes, per-family detection)."""
    false_eps, prev_false = 0, False
    for ts, p, label in flags:
        flagged = p >= threshold
        if flagged and label is None:
            if not prev_false:
                false_eps += 1
            prev_false = True
        else:
            prev_false = False
    quiet = sum(1 for _, _, l in flags if l is None)
    return false_eps, quiet, {}


# ============================================================================================ main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rudhabot.ml.security.train")
    parser.add_argument("--data", required=True, help="folder holding (or receiving) the public datasets")
    parser.add_argument("--download", action="store_true", help="download missing datasets (checksums verified)")
    parser.add_argument("--out", default=DEFAULT_DIR)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--train-servers", type=int, default=12)
    parser.add_argument("--test-servers", type=int, default=6)
    parser.add_argument("--days", type=int, default=40)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--quick", action="store_true", help="a small run to check the pipeline")
    args = parser.parse_args(argv)
    if args.quick:
        args.train_servers, args.test_servers, args.days, args.epochs = 2, 1, 6, 2
    os.makedirs(args.out, exist_ok=True)
    started = time.time()
    rng = random.Random(args.seed)
    report: dict = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seed": args.seed,
                    "config": vars(args), "data_policy": "public licence-checked datasets + synthetic chat; no "
                    "Rudhabot server data; raw text not committed"}
    # ------------------------------------------------------------------------------------ 1. message model
    if args.download:
        D.fetch(args.data)
    print("loading datasets ...")
    public = D.load_public(args.data, seed=args.seed)
    synthetic = D.synthetic_chat(args.seed + 100, 20000)
    seen = {e.group for e in public + synthetic}
    hard = [e for e in D.synthetic_chat(args.seed + 999, 8000) if e.group not in seen]
    examples = public + synthetic
    report["data"] = D.summary(examples)
    report["data"]["hard_negatives_test"] = len(hard)
    train = [e for e in examples if e.split == "train"]
    val = [e for e in examples if e.split == "val"]
    test = [e for e in examples if e.split == "test"]
    print(f"  {len(train)} train, {len(val)} val, {len(test)} test, {len(hard)} held-out synthetic chat")
    train_data, train_rows = build(train, rng, augment_positive=4, augment_negative=1)
    val_data, _ = build(val, rng)
    test_data, test_rows = build(test, rng)
    print(f"  {len(train_rows)} training rows after augmentation")
    net = HashedMultiTaskNet(seed=args.seed)
    print(f"message model: {net.params:,} parameters")
    t0 = time.time()
    history = net.fit(train_data, epochs=args.epochs, val=val_data, seed=args.seed, lr=5e-4, token_dropout=0.5)
    train_s = time.time() - t0
    raw_val = net.raw_logits(val_data[0], val_data[1])
    for j in range(len(HEADS)):
        m = val_data[3][:, j] > 0
        if m.any():
            net.calibration[j] = fit_platt(raw_val[m, j], val_data[2][m, j])
    metrics, p_test = evaluate_heads(net, test_data, test_rows)
    by_source = {}
    for source in sorted({r.source for r in test_rows}):
        keep = np.array([r.source == source for r in test_rows])
        sub = (None, None, test_data[2][keep], test_data[3][keep], None)
        entry = {}
        for j, head in enumerate(HEADS):
            m = sub[3][:, j] > 0
            if m.any():
                yy, pp = sub[2][m, j], p_test[keep][m, j]
                entry[head] = {"n": int(m.sum()), "positives": int(yy.sum()), "auc": auc(yy, pp),
                               f"at_{SIGNAL_AT}": at_threshold(yy, pp, SIGNAL_AT)}
        by_source[source] = entry
    # the rules alone on the same Discord test messages (link analysis score >= 0.5 as a scam prediction)
    discord = [(r, i) for i, r in enumerate(test_rows) if r.source.startswith("discord")]
    y_d = np.array([r.labels["scam"] for r, _ in discord], np.float32)
    rules = np.array([analyse_text(r.text).score for r, _ in discord], np.float32)
    model_d = np.array([p_test[i, 0] for _, i in discord], np.float32)
    either = np.maximum(rules >= 0.5, model_d >= SIGNAL_AT).astype(np.float32)
    rules_vs_model = {"n": len(discord), "positives": int(y_d.sum()),
                      "rules_only": at_threshold(y_d, rules, 0.5),
                      "model_only": at_threshold(y_d, model_d, SIGNAL_AT),
                      "rules_or_model": at_threshold(y_d, either, 0.5)}
    hard_data, hard_rows = build(hard, rng)
    p_hard = net.predict(hard_data[0], hard_data[1])
    hard_fp = {h: {f"over_{t}": round(float((p_hard[:, j] >= t).mean()), 5) for t in (SIGNAL_AT, 0.97)}
               for j, h in enumerate(HEADS[:2])}
    from rudhabot.ml.security.hashing import encode_messages
    sample = [r.text for r in test_rows[:300] if r.kind == "message"][:200]
    t0 = time.perf_counter()
    for text in sample:
        net.predict(*encode_messages([text]))
    latency_ms = (time.perf_counter() - t0) / max(1, len(sample)) * 1000
    version = "message-risk-v1"
    net.meta = {"version": version, "schema": HASH_SCHEMA}
    net.save(os.path.join(args.out, f"{version}.npz"))
    report["message"] = {"version": version, "params": net.params, "history": history, "train_seconds": round(train_s, 1),
                         "calibration": net.calibration.tolist(), "test": metrics, "by_source": by_source,
                         "discord_rules_vs_model": rules_vs_model, "held_out_synthetic_chat_flag_rate": hard_fp,
                         "latency_ms_per_message": round(latency_ms, 3)}
    print(json.dumps({h: {k: v for k, v in m.items() if k in ("auc", "average_precision", "ece")}
                      for h, m in metrics.items()}))
    # ------------------------------------------------------------------------------------ 2. wargame (rules only)
    train_families = tuple(f for f in FAMILIES if f not in HELD_OUT)
    train_jobs = [(args.seed * 1000 + i, args.days, train_families, 1200, False, args.out)
                  for i in range(args.train_servers)]
    test_jobs = [(args.seed * 1000 + 500 + i, args.days, FAMILIES, random.Random(i).choice((1000, 1500, 2500)), False,
                  args.out) for i in range(args.test_servers)]
    print(f"wargame: {len(train_jobs)} training servers, {len(test_jobs)} test servers, {args.days} days each ...")
    t0 = time.time()
    train_parts = _pool_map(train_jobs, args.workers)
    test_parts = _pool_map(test_jobs, args.workers)
    sim_s = time.time() - t0
    # ------------------------------------------------------------------------------------ 3. incident model
    family_index = {f: i for i, f in enumerate(FAMILIES)}

    def arrays(parts):
        X = np.concatenate([p["X"] for p in parts])
        labels = [l for p in parts for l in p["labels"]]
        yb = np.array([l is not None for l in labels], np.float32)
        yf = np.array([family_index[l] if l is not None else -1 for l in labels], np.int64)
        return X, yb, yf, labels
    Xtr, ybtr, yftr, _ = arrays(train_parts[:-1] if len(train_parts) > 1 else train_parts)
    Xva, ybva, yfva, _ = arrays(train_parts[-1:])
    Xte, ybte, yfte, labels_te = arrays(test_parts)
    inet = IncidentNet(DIM, seed=args.seed)
    print(f"incident model: {inet.params:,} parameters, {len(Xtr):,} training minutes "
          f"({int(ybtr.sum()):,} under attack)")
    ihistory = inet.fit(Xtr, ybtr, yftr, val=(Xva, ybva), seed=args.seed)
    zb = inet.raw(Xva)
    inet.calibration = np.array(fit_platt(zb, ybva), np.float32)
    p_te, fam_te = inet.predict(Xte)
    per_family = {}
    for fam in FAMILIES:
        m = np.array([l == fam for l in labels_te])
        if m.any():
            per_family[fam] = {"minutes": int(m.sum()), "held_out": fam in HELD_OUT,
                               "minute_recall_at_0.8": round(float((p_te[m] >= SIGNAL_AT).mean()), 4),
                               "family_accuracy": round(float((fam_te[m].argmax(1) == family_index[fam]).mean()), 4)}
    quiet = ybte == 0
    inc_version = "incident-v1"
    inet.meta = {"version": inc_version, "schema": FEATURE_SCHEMA, "families": list(FAMILIES)}
    inet.save(os.path.join(args.out, f"{inc_version}.npz"))
    report["incident"] = {"version": inc_version, "params": inet.params, "history": ihistory,
                          "train_minutes": int(len(Xtr)), "test_minutes": int(len(Xte)),
                          "auc": auc(ybte, p_te), "average_precision": average_precision(ybte, p_te),
                          "ece": round(ece(ybte, p_te), 4), f"at_{SIGNAL_AT}": at_threshold(ybte, p_te, SIGNAL_AT),
                          "quiet_minute_flag_rate": round(float((p_te[quiet] >= SIGNAL_AT).mean()), 5),
                          "per_family": per_family, "held_out_families": list(HELD_OUT)}
    print(json.dumps({k: report["incident"][k] for k in ("auc", "average_precision", "ece", "quiet_minute_flag_rate")}))
    # ------------------------------------------------------------------------------------ 4. alert ranker
    # every incident (alerted or digest-only), read 5 minutes after it opened: ranking matters most where staff must
    # pick the real ones out of many weak flags
    ranker = AlertRanker(RANK_FEATURES)
    Xr = np.array([v for p in train_parts for v, _, _ in p["ranked"]], np.float32)
    yr = np.array([t for p in train_parts for _, t, _ in p["ranked"]], np.float32)
    Xrt = np.array([v for p in test_parts for v, _, _ in p["ranked"]], np.float32)
    yrt = np.array([t for p in test_parts for _, t, _ in p["ranked"]], np.float32)
    alerted_t = np.array([a for p in test_parts for _, _, a in p["ranked"]], bool)
    ranker.fit(Xr, yr)
    s_rt = ranker.scores(Xrt) if len(Xrt) else np.zeros(0)
    ranker.meta = {"version": "ranker-v1", "train_incidents": int(len(yr)), "train_true": int(yr.sum())}
    with open(os.path.join(args.out, "ranker-v1.json"), "w", encoding="utf-8") as handle:
        json.dump(ranker.to_dict(), handle)
    severity_index = RANK_FEATURES.index("rule_severity")
    model_index = RANK_FEATURES.index("model_bonus")
    severity_total = Xrt[:, severity_index] + 0.15 * Xrt[:, model_index] if len(Xrt) else np.zeros(0)
    report["ranker"] = {"version": "ranker-v1", "train_incidents": int(len(yr)), "test_incidents": int(len(yrt)),
                        "test_true": int(yrt.sum()), "test_alerted": int(alerted_t.sum()),
                        "auc": auc(yrt, s_rt) if len(yrt) else None,
                        "auc_of_severity_alone": auc(yrt, severity_total) if len(yrt) else None,
                        "auc_alerted_only": auc(yrt[alerted_t], s_rt[alerted_t]) if alerted_t.any() else None,
                        "auc_alerted_only_severity": auc(yrt[alerted_t], severity_total[alerted_t]) if alerted_t.any() else None,
                        "auc_digest_only": auc(yrt[~alerted_t], s_rt[~alerted_t]) if (~alerted_t).any() else None,
                        "auc_digest_only_severity": auc(yrt[~alerted_t], severity_total[~alerted_t]) if (~alerted_t).any() else None,
                        "top_weights": sorted(((n, round(float(w), 3)) for n, w in zip(RANK_FEATURES, ranker.w)),
                                              key=lambda x: -abs(x[1]))[:10]}
    # ------------------------------------------------------------------------------------ 5. full system
    manifest = {"message": {"file": f"{version}.npz", "version": version, "schema": HASH_SCHEMA, "params": net.params},
                "incident": {"file": f"{inc_version}.npz", "version": inc_version, "schema": FEATURE_SCHEMA,
                             "params": inet.params},
                "ranker": {"file": "ranker-v1.json", "version": "ranker-v1"},
                "created": report["created"], "data_policy": report["data_policy"]}
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    print("wargame with models ...")
    model_jobs = [(seed, days, fams, members, True, args.out) for seed, days, fams, members, _, _ in test_jobs]
    model_parts = _pool_map(model_jobs, args.workers)
    rules_only = merge_summaries(test_parts)
    with_models = merge_summaries(model_parts)
    flags = [f for p in model_parts for f in p["flags"]]
    false_eps, quiet_minutes, _ = episodes(flags, SIGNAL_AT)
    report["system"] = {"test_servers": len(test_jobs), "days_per_server": args.days, "simulation_seconds": round(sim_s, 1),
                        "rules_only": rules_only, "rules_plus_models": with_models,
                        "incident_model_false_episodes_per_quiet_day": round(false_eps / max(1, quiet_minutes / 1440), 3),
                        "training_servers_rules_only": merge_summaries(train_parts)}
    report["seconds"] = round(time.time() - started, 1)
    with open(os.path.join(args.out, "benchmark.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, default=str)
    with open(os.path.join(args.out, "BENCHMARK.md"), "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    print(f"done in {report['seconds']} s -> {args.out}")
    return 0


def _pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def _f(x, digits=3):
    return "n/a" if x is None else f"{x:.{digits}f}"


def render_markdown(r: dict) -> str:
    m, inc, rk, sysr = r["message"], r["incident"], r["ranker"], r["system"]
    L = [f"# Security models benchmark ({r['created']})", "",
         "All numbers come from `python -m rudhabot.ml.security.train`. The message model is tested on held-out "
         "public data. The incident model, ranker and full system are tested on *simulated* servers. Simulation "
         "measures behaviour against modelled attacks; it doesn't prove real-world accuracy, so the bot starts in "
         "shadow mode.", "",
         f"## Message-risk model ({m['version']}, {m['params']:,} parameters)", "",
         "| head | test n | positives | AUC | avg precision | ECE | precision @0.8 (95% CI) | recall @0.8 (95% CI) |",
         "|---|---|---|---|---|---|---|---|"]
    for head, x in m["test"].items():
        t = x[f"at_{SIGNAL_AT}"]
        L.append(f"| {head} | {x['n']} | {x['positives']} | {_f(x['auc'])} | {_f(x['average_precision'])} | "
                 f"{x['ece']:.3f} | {t['precision']:.3f} ({t['precision_ci95'][0]:.2f}-{t['precision_ci95'][1]:.2f}) | "
                 f"{t['recall']:.3f} ({t['recall_ci95'][0]:.2f}-{t['recall_ci95'][1]:.2f}) |")
    rv = m["discord_rules_vs_model"]
    L += ["", f"Discord scam test messages ({rv['n']}, {rv['positives']} scams): rules alone catch "
          f"{rv['rules_only']['recall']:.0%} at precision {rv['rules_only']['precision']:.0%}; the model alone "
          f"{rv['model_only']['recall']:.0%} at {rv['model_only']['precision']:.0%}; either "
          f"{rv['rules_or_model']['recall']:.0%} at {rv['rules_or_model']['precision']:.0%}.",
          f"Held-out synthetic chat flagged at >= 0.8: scam {m['held_out_synthetic_chat_flag_rate']['scam'][f'over_{SIGNAL_AT}']:.2%}, "
          f"spam {m['held_out_synthetic_chat_flag_rate']['spam'][f'over_{SIGNAL_AT}']:.2%}. "
          f"Latency {m['latency_ms_per_message']} ms per message.", "",
          f"## Incident model ({inc['version']}, {inc['params']:,} parameters)", "",
          f"Per-minute test AUC {_f(inc['auc'])}, average precision {_f(inc['average_precision'])}, ECE {inc['ece']:.3f}; "
          f"quiet minutes flagged {inc['quiet_minute_flag_rate']:.2%}. Held-out families: {', '.join(inc['held_out_families'])}.", "",
          "| family | held out | attack minutes | minutes flagged @0.8 | family correct |", "|---|---|---|---|---|"]
    for fam, x in inc["per_family"].items():
        L.append(f"| {fam} | {'yes' if x['held_out'] else ''} | {x['minutes']} | {x['minute_recall_at_0.8']:.1%} | "
                 f"{x['family_accuracy']:.1%} |")
    L += ["", "## Alert ranker", "",
          f"On {rk['test_incidents']} test incidents ({rk['test_true']} real, {rk['test_alerted']} alerted): AUC "
          f"{_f(rk['auc'])} vs {_f(rk['auc_of_severity_alone'])} for severity alone; among digest-only flags "
          f"{_f(rk['auc_digest_only'])} vs {_f(rk['auc_digest_only_severity'])}; among alerts "
          f"{_f(rk['auc_alerted_only'])} vs {_f(rk['auc_alerted_only_severity'])}.", "",
          f"## Full system on {sysr['test_servers']} simulated test servers x {sysr['days_per_server']} days", "",
          "| family | n | alerted (rules) | alerted (+models) | median latency s | messages deleted | contained | "
          "actions before containment |", "|---|---|---|---|---|---|---|---|"]
    ro, wm = sysr["rules_only"]["families"], sysr["rules_plus_models"]["families"]
    for fam in FAMILIES:
        a, b = ro.get(fam), wm.get(fam, {})
        if not a:
            continue
        L.append(f"| {fam} | {a['n']} | {a['alerted']:.0%} | {b.get('alerted', 0):.0%} | {_f(a['median_latency_s'], 0)} | "
                 f"{_pct(b.get('messages_deleted', a.get('messages_deleted')))} | {_pct(a.get('contained'))} | "
                 f"{a.get('median_actions_before_containment', 'n/a')} |")
    L += ["", "Per simulated day (rules only / rules + models):", ""]
    keys = sorted(set(sysr["rules_only"]["per_day"]) | set(sysr["rules_plus_models"]["per_day"]))
    for k in keys:
        L.append(f"- {k.replace('_', ' ')}: {sysr['rules_only']['per_day'].get(k, 0):.2f} / "
                 f"{sysr['rules_plus_models']['per_day'].get(k, 0):.2f}")
    L += [f"- false alerts per quiet day: {sysr['rules_only']['false_alerts_per_quiet_day']:.2f} / "
          f"{sysr['rules_plus_models']['false_alerts_per_quiet_day']:.2f}",
          f"- incident-model false episodes per quiet day: {sysr['incident_model_false_episodes_per_quiet_day']}", ""]
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())

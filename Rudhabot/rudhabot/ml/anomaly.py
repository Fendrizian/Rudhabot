"""Clairvoyance anomaly detection: flags unusual economy activity *for human review*. It never changes balances,
blocks commands or punishes anyone.

Windows   Activity is summarised per user per hour from the audit ledger and the failed-interaction log:
          entries, money in, money out, largest single change, net change, distinct sources, gambling wins,
          transfers received from other people, failed interactions.  (`FEATURES`; log1p-scaled)
Model     An isolation forest (numpy, 100 trees × 256 samples) fitted on the server's own recent hourly windows, so
          "unusual" means unusual *for this server*, plus an extreme-deviation test: a window is also flagged when a
          suspicious feature (money in, largest change, net change, gambling wins, transfers received, failures) is
          at least Z_EXTREME robust standard deviations above this server's median. The forest alone dilutes one
          extreme feature among nine; the z-test catches it. Each flag explains itself with the features that
          deviate most (robust z-scores: median / MAD, with a floor so a feature that is usually zero doesn't turn
          a single event into "z 693").
Rules     Hard limits that apply even before there is enough history to fit the forest (cold start), e.g. a
          storm of failed interactions or a single enormous balance change.
Eval      `python -m rudhabot.ml.anomaly` runs synthetic populations with injected anomalies (clearly synthetic:
          no real user data). THRESHOLD and Z_EXTREME are tuned on seeds 0-9 and reported on untouched seeds
          100-119 (precision, recall, per anomaly kind).
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

import numpy as np

FEATURES = ("entries", "inflow", "outflow", "max_change", "net", "sources", "gamble_wins", "received_from_others",
            "failures")
# Audit reasons that mean "a game of chance paid out". Payouts made inside the slash command are labelled "/name";
# payouts made from a game's buttons are labelled by its view ("Blackjack (button)"). Both forms must be listed:
# "/crash", "/highlow", "/blackjack" and "/roulette" were missing, so those wins were not counted as gambling wins.
GAMBLING = ("/slots", "/coinflip", "/dice", "/rps", "/baccarat", "/craps", "/blackjack", "/roulette", "/highlow", "/crash",
            "Blackjack", "Roulette", "HighLow", "Crash")


def _c(n: int) -> float:
    """Average path length of an unsuccessful BST search (isolation-forest normaliser)."""
    if n <= 1:
        return 0.0
    return 2 * (math.log(n - 1) + 0.5772156649) - 2 * (n - 1) / n


class IsolationForest:
    def __init__(self, n_trees: int = 100, sample: int = 256, seed: int = 0):
        self.n_trees, self.sample, self.rng = n_trees, sample, np.random.default_rng(seed)
        self.trees: list = []
        self._n = 0

    def _build(self, X, depth, limit):
        if depth >= limit or len(X) <= 1:
            return ("leaf", len(X))
        spread = X.max(0) - X.min(0)
        cols = np.flatnonzero(spread > 0)
        if not len(cols):
            return ("leaf", len(X))
        f = int(self.rng.choice(cols))
        split = float(self.rng.uniform(X[:, f].min(), X[:, f].max()))
        left = X[:, f] < split
        return ("node", f, split, self._build(X[left], depth + 1, limit), self._build(X[~left], depth + 1, limit))

    def fit(self, X: np.ndarray) -> "IsolationForest":
        n = min(self.sample, len(X))
        self._n = n
        limit = math.ceil(math.log2(max(2, n)))
        self.trees = [self._build(X[self.rng.choice(len(X), n, replace=False)], 0, limit) for _ in range(self.n_trees)]
        return self

    def _path(self, x, node, depth=0):
        if node[0] == "leaf":
            return depth + _c(node[1])
        _, f, split, left, right = node
        return self._path(x, left if x[f] < split else right, depth + 1)

    def score(self, X: np.ndarray) -> np.ndarray:
        """Anomaly score in (0, 1]: ~0.5 is ordinary, close to 1 is very isolated."""
        paths = np.array([[self._path(x, t) for t in self.trees] for x in X]).mean(1)
        return 2 ** (-paths / _c(self._n))


@dataclass
class Window:
    user: str
    hour: str
    values: dict

    def vector(self) -> np.ndarray:
        return np.log1p(np.array([max(0.0, float(self.values.get(f, 0))) for f in FEATURES], dtype=np.float64))


def windows(entries: Iterable[dict], failures: Iterable[dict] = ()) -> list[Window]:
    """Hourly per-user windows from audit entries ({user, ts, delta, reason, actor, account}) and failure events
    ({user, ts}). Only wallet/bank/savings money movements count toward the money features."""
    acc: dict = defaultdict(lambda: defaultdict(float))
    sources: dict = defaultdict(set)
    for e in entries:
        if e.get("account") not in ("wallet", "bank", "savings", "credit"):
            continue
        key = (e["user"], e["ts"][:13])
        w = acc[key]
        delta = float(e["delta"])
        w["entries"] += 1
        w["inflow" if delta > 0 else "outflow"] += abs(delta)
        w["max_change"] = max(w["max_change"], abs(delta))
        w["net"] += delta
        sources[key].add(e.get("reason", "?").split(" ")[0])
        if delta > 0 and str(e.get("reason", "")).startswith(GAMBLING):
            w["gamble_wins"] += 1
        if delta > 0 and e.get("actor") and e["actor"] != e["user"]:
            w["received_from_others"] += 1
    for f in failures:
        acc[(f["user"], f["ts"][:13])]["failures"] += 1
    out = []
    for (user, hour), values in acc.items():
        values["sources"] = len(sources[(user, hour)])
        values["net"] = abs(values["net"])
        out.append(Window(user, hour, dict(values)))
    return out


@dataclass(frozen=True)
class Flag:
    user: str
    hour: str
    score: float
    reasons: tuple[str, ...]
    kind: str                     # "model" | "rule"


EXTREME_FEATURES = ("inflow", "max_change", "net", "gamble_wins", "received_from_others", "failures")


class AnomalyDetector:
    """Fit on recent windows, then judge new windows. Thresholds tuned on the synthetic benchmark (see main()).

    Personal baselines: a member with at least MIN_USER_WINDOWS hourly windows is judged relative to their *own*
    usual activity (their median window is moved onto the server's median before scoring). A high-roller's normal
    big bets are then ordinary, while a sudden change in anyone's behaviour still stands out. The hard rules and the
    raw extreme test (Z_RAW, on uncentred values) still apply to everyone, so persistent extreme behaviour can't
    hide behind its own baseline."""
    MIN_WINDOWS = 120
    MIN_USER_WINDOWS = 5
    THRESHOLD = 0.62              # isolation-forest score (on baseline-centred windows)
    Z_EXTREME = 7.0               # robust z of one suspicious feature, baseline-centred
    Z_RAW = 8.0                   # robust z of one suspicious feature, uncentred (no personal baseline)
    SCALE_FLOOR = 0.5             # minimum robust spread in log1p units (≈ a 1.6x change)
    RULES = {"failures": 15, "max_change": 1_000_000, "received_from_others": 12}

    def __init__(self, seed: int = 0, threshold: Optional[float] = None, z_extreme: Optional[float] = None):
        self.forest: Optional[IsolationForest] = None
        self.median = self.mad = None
        self.baselines: dict[str, np.ndarray] = {}
        self.seed = seed
        self.fitted_on = 0
        self.threshold = self.THRESHOLD if threshold is None else threshold
        self.z_extreme = self.Z_EXTREME if z_extreme is None else z_extreme
        self._extreme = np.array([f in EXTREME_FEATURES for f in FEATURES])

    def fit(self, population: list[Window]) -> bool:
        if len(population) < self.MIN_WINDOWS:
            self.forest = None
            self.fitted_on = len(population)
            return False
        X = np.stack([w.vector() for w in population])
        self.median = np.median(X, 0)
        by_user: dict[str, list[int]] = defaultdict(list)
        for i, w in enumerate(population):
            by_user[w.user].append(i)
        self.baselines = {u: np.median(X[idx], 0) - self.median for u, idx in by_user.items()
                          if len(idx) >= self.MIN_USER_WINDOWS}
        C = np.stack([self._centred(w, x) for w, x in zip(population, X)])
        self.forest = IsolationForest(seed=self.seed).fit(C)
        self.mad = np.maximum(np.median(np.abs(C - self.median), 0) * 1.4826, self.SCALE_FLOOR)
        self.fitted_on = len(population)
        return True

    def _centred(self, w: Window, x: Optional[np.ndarray] = None) -> np.ndarray:
        x = w.vector() if x is None else x
        offset = self.baselines.get(w.user)
        return x if offset is None else x - offset

    def zscores(self, w: Window, centred: bool = True) -> np.ndarray:
        return ((self._centred(w) if centred else w.vector()) - self.median) / self.mad

    def explain(self, w: Window, k: int = 3) -> tuple[str, ...]:
        if self.median is None:
            return ()
        z = np.maximum(self.zscores(w), self.zscores(w, centred=False) * (self.Z_EXTREME / self.Z_RAW))
        top = np.argsort(-z)[:k]
        return tuple(f"{FEATURES[i]}={w.values.get(FEATURES[i], 0):,.0f} (z {z[i]:.1f})" for i in top if z[i] > 2)

    def judge(self, w: Window) -> Optional[Flag]:
        rule_hits = [f"{name} {w.values.get(name, 0):,.0f} ≥ {limit:,}" for name, limit in self.RULES.items()
                     if w.values.get(name, 0) >= limit]
        if rule_hits:
            return Flag(w.user, w.hour, 1.0, tuple(rule_hits) + self.explain(w), "rule")
        if self.forest is None:
            return None
        s = float(self.forest.score(self._centred(w)[None])[0])
        extreme = float(np.max(self.zscores(w)[self._extreme]))
        raw = float(np.max(self.zscores(w, centred=False)[self._extreme]))
        if s >= self.threshold or extreme >= self.z_extreme or raw >= self.Z_RAW:
            reasons = self.explain(w)
            if reasons:                       # a high score with nothing above normal is not actionable
                return Flag(w.user, w.hour, s, reasons, "model")
        return None


# ------------------------------------------------------------------ synthetic benchmark
ANOMALY_KINDS = ("money mule", "gambling streak", "failure storm", "huge movement")


def synthetic(seed: int = 0, users: int = 150, hours: int = 72, anomalies: int = 30, with_kinds: bool = False):
    """A synthetic server: ordinary players (work, gambling, shopping) plus injected anomalies of four kinds.

    Realism that makes the benchmark meaningful: 5% of ordinary players are legitimate high-rollers who bet and spend
    20x more (they must *not* be flagged), and every anomaly has a random intensity between 35% and 100%, so some are
    subtle (a money mule receiving 5 transfers, a short winning streak). Returns (entries, failures, truth), plus
    {(user, hour): kind} with `with_kinds=True`."""
    from datetime import timedelta
    rng = random.Random(seed)
    entries, failures, truth, kinds = [], [], set(), {}
    start = datetime(2026, 1, 1)
    for u in range(users):
        activity = rng.uniform(0.1, 0.6)
        scale = 20 if rng.random() < 0.05 else 1          # a legitimate high-roller
        for h in range(hours):
            if rng.random() > activity:
                continue
            ts = (start + timedelta(hours=h)).isoformat()
            for _ in range(rng.randint(1, 6)):
                kind = rng.random()
                if kind < 0.3:
                    entries.append({"user": str(u), "ts": ts, "account": "wallet", "delta": rng.randint(20, 400), "reason": "/work"})
                elif kind < 0.7:
                    bet = rng.randint(10, 800) * scale
                    entries.append({"user": str(u), "ts": ts, "account": "wallet", "delta": bet if rng.random() < 0.47 else -bet,
                                    "reason": "/slots"})
                else:
                    entries.append({"user": str(u), "ts": ts, "account": "wallet", "delta": -rng.randint(20, 900) * scale,
                                    "reason": "/buy"})
            if rng.random() < 0.05:
                failures.append({"user": str(u), "ts": ts})
    for a in range(anomalies):
        u, h = str(rng.randrange(users)), rng.randrange(hours)
        ts = (start + timedelta(hours=h)).isoformat()
        kind = a % 4
        k = rng.uniform(0.35, 1.0)                          # intensity: some anomalies are subtle
        if kind == 0:                                   # money mule: many transfers in from different people
            for i in range(max(4, int(rng.randint(8, 14) * k))):
                entries.append({"user": u, "ts": ts, "account": "wallet", "delta": int(rng.randint(2000, 9000) * k),
                                "reason": "/send", "actor": f"x{i}"})
        elif kind == 1:                                 # implausible gambling streak
            for _ in range(max(8, int(rng.randint(25, 40) * k))):
                entries.append({"user": u, "ts": ts, "account": "wallet", "delta": int(rng.randint(3000, 8000) * k),
                                "reason": "/slots"})
        elif kind == 2:                                 # failure storm (scripted clicking, probing)
            failures.extend({"user": u, "ts": ts} for _ in range(max(6, int(rng.randint(10, 25) * k))))
        else:                                           # single huge movement
            entries.append({"user": u, "ts": ts, "account": "bank", "delta": int(rng.randint(400_000, 2_000_000) * k),
                            "reason": "/add-money"})
        truth.add((u, ts[:13]))
        kinds.setdefault((u, ts[:13]), ANOMALY_KINDS[kind])
    return (entries, failures, truth, kinds) if with_kinds else (entries, failures, truth)


TUNING_SEEDS = range(0, 10)
HELD_OUT_SEEDS = range(100, 120)


def _scored(seed: int):
    """Score every window of one synthetic server once: (truth, kinds, rows), row = (key, rule_hit, forest score,
    max extreme z, has reasons). Thresholds can then be swept without refitting or rescoring."""
    entries, failures, truth, kinds = synthetic(seed, with_kinds=True)
    ws = windows(entries, failures)
    det = AnomalyDetector(seed=seed)
    det.fit(ws)
    scores = det.forest.score(np.stack([det._centred(w) for w in ws]))
    rows = []
    for w, sc in zip(ws, scores):
        rule = any(w.values.get(name, 0) >= limit for name, limit in det.RULES.items())
        raw = float(np.max(det.zscores(w, centred=False)[det._extreme])) >= det.Z_RAW
        rows.append(((w.user, w.hour), rule or raw, float(sc), float(np.max(det.zscores(w)[det._extreme])),
                     bool(det.explain(w))))
    return truth, kinds, rows


def _evaluate(scored, threshold: float, z_extreme: float) -> dict:
    tp = fp = fn = windows_seen = 0
    per_kind = {k: [0, 0] for k in ANOMALY_KINDS}             # [caught, injected]
    for truth, kinds, rows in scored:
        windows_seen += len(rows)
        flagged = {key for key, rule, sc, z, reasons in rows if rule or ((sc >= threshold or z >= z_extreme) and reasons)}
        tp += len(flagged & truth); fp += len(flagged - truth); fn += len(truth - flagged)
        for key, kind in kinds.items():
            per_kind[kind][1] += 1
            per_kind[kind][0] += key in flagged
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "tp": tp, "fp": fp, "fn": fn, "windows": windows_seen,
            "recall_by_kind": {k: c / n if n else None for k, (c, n) in per_kind.items()}}


def benchmark(seeds, threshold: Optional[float] = None, z_extreme: Optional[float] = None, scored=None) -> dict:
    """Precision / recall (overall and per anomaly kind) over synthetic servers; same decision rule as judge()."""
    scored = scored if scored is not None else [_scored(s) for s in seeds]
    return _evaluate(scored, AnomalyDetector.THRESHOLD if threshold is None else threshold,
                     AnomalyDetector.Z_EXTREME if z_extreme is None else z_extreme)


def tune(seeds=TUNING_SEEDS, min_precision: float = 0.95) -> tuple[float, float, list]:
    """The (forest threshold, z bar) with the best F1 at precision >= min_precision on the tuning seeds."""
    scored = [_scored(s) for s in seeds]
    grid = []
    for th in (0.54, 0.56, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70, 0.72, 0.75, 1.01):
        for z in (3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, float("inf")):
            r = _evaluate(scored, th, z)
            grid.append({"threshold": th, "z_extreme": z, "precision": r["precision"], "recall": r["recall"], "f1": r["f1"]})
    ok = [g for g in grid if g["precision"] >= min_precision] or grid
    top = max(g["f1"] for g in ok)
    # among settings within 0.005 F1 of the best, prefer the highest bars: a real server's tails are heavier than any
    # synthetic one, so the most conservative of the equally good settings raises the fewest false alarms
    best = max((g for g in ok if g["f1"] >= top - 0.005), key=lambda g: (g["z_extreme"], g["threshold"]))
    return best["threshold"], best["z_extreme"], grid


def main() -> None:
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tune", action="store_true", help="re-run the threshold grid on the tuning seeds")
    parser.add_argument("--out", default="", help="also write the report to this JSON file")
    args = parser.parse_args()
    report = {"note": "synthetic populations with injected anomalies (no real user data)",
              "settings": {"threshold": AnomalyDetector.THRESHOLD, "z_extreme": AnomalyDetector.Z_EXTREME,
                           "scale_floor": AnomalyDetector.SCALE_FLOOR}}
    if args.tune:
        th, z, grid = tune()
        report["tuning"] = {"seeds": [TUNING_SEEDS.start, TUNING_SEEDS.stop - 1], "chosen": {"threshold": th, "z_extreme": z},
                            "grid": grid}
    report["tuning_seeds"] = benchmark(TUNING_SEEDS)
    report["held_out_seeds"] = benchmark(HELD_OUT_SEEDS)
    report["held_out_seeds"]["seeds"] = [HELD_OUT_SEEDS.start, HELD_OUT_SEEDS.stop - 1]
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)


if __name__ == "__main__":
    main()

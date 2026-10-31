"""Clairvoyance anomaly detection: flags unusual economy activity *for human review*. It never changes balances,
blocks commands or punishes anyone.

Windows   Activity is summarised per user per hour from the audit ledger and the failed-interaction log:
          entries, money in, money out, largest single change, net change, distinct sources, gambling wins,
          transfers received from other people, failed interactions.  (`FEATURES`; log1p-scaled)
Model     An isolation forest (numpy, 100 trees × 256 samples) fitted on the server's own recent hourly windows, so
          "unusual" means unusual *for this server*. Each flag explains itself with the features that deviate most
          from the population (robust z-scores: median / MAD).
Rules     Hard limits that apply even before there is enough history to fit the forest (cold start), e.g. a
          storm of failed interactions or a single enormous balance change.
Eval      `python -m rudhabot.ml.anomaly` runs a synthetic population with injected anomalies (clearly synthetic:
          no real user data) and reports precision / recall of the threshold.
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
GAMBLING = ("/slots", "/coinflip", "/dice", "/rps", "/baccarat", "/craps", "Blackjack", "Roulette", "HighLow", "Crash")


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


class AnomalyDetector:
    """Fit on recent windows, then judge new windows. Thresholds chosen on the synthetic benchmark (see main())."""
    MIN_WINDOWS = 120
    THRESHOLD = 0.66
    RULES = {"failures": 15, "max_change": 1_000_000, "received_from_others": 12}

    def __init__(self, seed: int = 0):
        self.forest: Optional[IsolationForest] = None
        self.median = self.mad = None
        self.seed = seed
        self.fitted_on = 0

    def fit(self, population: list[Window]) -> bool:
        if len(population) < self.MIN_WINDOWS:
            self.forest = None
            self.fitted_on = len(population)
            return False
        X = np.stack([w.vector() for w in population])
        self.forest = IsolationForest(seed=self.seed).fit(X)
        self.median = np.median(X, 0)
        self.mad = np.median(np.abs(X - self.median), 0) * 1.4826 + 1e-3
        self.fitted_on = len(population)
        return True

    def explain(self, w: Window, k: int = 3) -> tuple[str, ...]:
        if self.median is None:
            return ()
        z = (w.vector() - self.median) / self.mad
        top = np.argsort(-z)[:k]
        return tuple(f"{FEATURES[i]}={w.values.get(FEATURES[i], 0):,.0f} (z {z[i]:.1f})" for i in top if z[i] > 2)

    def judge(self, w: Window) -> Optional[Flag]:
        rule_hits = [f"{name} {w.values.get(name, 0):,.0f} ≥ {limit:,}" for name, limit in self.RULES.items()
                     if w.values.get(name, 0) >= limit]
        if rule_hits:
            return Flag(w.user, w.hour, 1.0, tuple(rule_hits) + self.explain(w), "rule")
        if self.forest is None:
            return None
        s = float(self.forest.score(w.vector()[None])[0])
        if s >= self.THRESHOLD:
            reasons = self.explain(w)
            if reasons:                       # a high score with nothing above normal is not actionable
                return Flag(w.user, w.hour, s, reasons, "model")
        return None


# ------------------------------------------------------------------ synthetic benchmark
def synthetic(seed: int = 0, users: int = 150, hours: int = 72, anomalies: int = 30):
    """A synthetic server: ordinary players (work/daily/gambling/shopping) plus injected anomalies of four kinds."""
    rng = random.Random(seed)
    entries, failures, truth = [], [], set()
    start = datetime(2026, 1, 1)
    for u in range(users):
        activity = rng.uniform(0.1, 0.6)
        for h in range(hours):
            if rng.random() > activity:
                continue
            ts = (start.replace(hour=0) + __import__("datetime").timedelta(hours=h)).isoformat()
            for _ in range(rng.randint(1, 6)):
                kind = rng.random()
                if kind < 0.3:
                    entries.append({"user": str(u), "ts": ts, "account": "wallet", "delta": rng.randint(20, 400), "reason": "/work"})
                elif kind < 0.7:
                    bet = rng.randint(10, 800)
                    entries.append({"user": str(u), "ts": ts, "account": "wallet", "delta": bet if rng.random() < 0.47 else -bet,
                                    "reason": "/slots"})
                else:
                    entries.append({"user": str(u), "ts": ts, "account": "wallet", "delta": -rng.randint(20, 900), "reason": "/buy"})
            if rng.random() < 0.05:
                failures.append({"user": str(u), "ts": ts})
    for a in range(anomalies):
        u, h = str(rng.randrange(users)), rng.randrange(hours)
        ts = (start + __import__("datetime").timedelta(hours=h)).isoformat()
        kind = a % 4
        if kind == 0:                                   # money mule: many transfers in from different people
            for i in range(rng.randint(8, 14)):
                entries.append({"user": u, "ts": ts, "account": "wallet", "delta": rng.randint(2000, 9000), "reason": "/send",
                                "actor": f"x{i}"})
        elif kind == 1:                                 # implausible gambling streak
            for _ in range(rng.randint(25, 40)):
                entries.append({"user": u, "ts": ts, "account": "wallet", "delta": rng.randint(3000, 8000), "reason": "/slots"})
        elif kind == 2:                                 # failure storm (scripted clicking, probing)
            failures.extend({"user": u, "ts": ts} for _ in range(rng.randint(10, 25)))
        else:                                           # single huge movement
            entries.append({"user": u, "ts": ts, "account": "bank", "delta": rng.randint(400_000, 2_000_000), "reason": "/add-money"})
        truth.add((u, ts[:13]))
    return entries, failures, truth


def main() -> None:
    import json
    results = []
    for seed in range(5):
        entries, failures, truth = synthetic(seed)
        ws = windows(entries, failures)
        det = AnomalyDetector(seed=seed)
        det.fit(ws)
        flagged = {(f.user, f.hour) for w in ws if (f := det.judge(w))}
        tp = len(flagged & truth)
        results.append({"seed": seed, "windows": len(ws), "anomalies": len(truth), "flagged": len(flagged),
                        "precision": tp / len(flagged) if flagged else 0.0, "recall": tp / len(truth)})
    summary = {k: float(np.mean([r[k] for r in results])) for k in ("precision", "recall", "flagged", "windows")}
    print(json.dumps({"runs": results, "mean": summary, "note": "synthetic population with injected anomalies"}, indent=2))


if __name__ == "__main__":
    main()

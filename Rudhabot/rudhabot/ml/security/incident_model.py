"""The incident model and the alert ranker.

IncidentNet: every minute, the 83 multi-scale activity features (security.features) -> a small MLP with two heads:
P(an attack is under way) and which kind (softmax over the 8 attack families). Trained on wargame days whose attacks
come from 6 families; 2 families (slow_drip, malicious_bot) are held out entirely and appear only in the test servers,
which measures whether it recognises an attack *pattern* rather than memorising the training families. Its output is
advisory: confident readings become a model signal that can add at most 0.15 to an open incident's severity, and a
model-only reading only reaches the digest.

AlertRanker: logistic regression over an incident's evidence (best score per detector, accounts, channels, signals,
threat level, family) -> how likely the alert is a real attack. Staff see alerts ordered by it, with the
contributions (weight x value) of the strongest features as the "why ranked here" reason. It never filters alerts out.
"""
from __future__ import annotations

import io
import json
import math
from typing import Optional

import numpy as np

from rudhabot.ml.security.sim import FAMILIES


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def _softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


class IncidentNet:
    ARCH = "incident-mlp-v1"

    def __init__(self, dim: int, hidden: tuple[int, int] = (64, 32), seed: int = 0):
        rng = np.random.default_rng(seed)
        self.dim, self.hidden = dim, hidden
        h1, h2 = hidden
        k = len(FAMILIES)
        self.mu = np.zeros(dim, np.float32)
        self.sd = np.ones(dim, np.float32)
        self.W1 = (rng.standard_normal((dim, h1)) * math.sqrt(2 / dim)).astype(np.float32)
        self.b1 = np.zeros(h1, np.float32)
        self.W2 = (rng.standard_normal((h1, h2)) * math.sqrt(2 / h1)).astype(np.float32)
        self.b2 = np.zeros(h2, np.float32)
        self.Wb = (rng.standard_normal((h2, 1)) * math.sqrt(1 / h2)).astype(np.float32)
        self.bb = np.zeros(1, np.float32)
        self.Wf = (rng.standard_normal((h2, k)) * math.sqrt(1 / h2)).astype(np.float32)
        self.bf = np.zeros(k, np.float32)
        self.calibration = np.array([1.0, 0.0], np.float32)
        self.meta: dict = {}

    PARAMS = ("W1", "b1", "W2", "b2", "Wb", "bb", "Wf", "bf")

    @property
    def params(self) -> int:
        return int(sum(getattr(self, k).size for k in self.PARAMS))

    def _forward(self, X):
        x = (X - self.mu) / self.sd
        a1 = x @ self.W1 + self.b1
        h1 = np.maximum(a1, 0)
        a2 = h1 @ self.W2 + self.b2
        h2 = np.maximum(a2, 0)
        return x, a1, h1, a2, h2, (h2 @ self.Wb + self.bb)[:, 0], h2 @ self.Wf + self.bf

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(P(attack), P(family | attack)) for each row."""
        X = np.atleast_2d(np.asarray(X, np.float32))
        *_, zb, zf = self._forward(X)
        a, b = self.calibration
        return _sigmoid(a * zb + b), _softmax(zf)

    def raw(self, X):
        return self._forward(np.atleast_2d(np.asarray(X, np.float32)))[5]

    def fit(self, X, yb, yf, *, epochs=30, batch=512, lr=2e-3, l2=1e-4, seed=0, val=None, patience=4, pos_weight=None,
            log=print) -> list[dict]:
        X = np.asarray(X, np.float32)
        self.mu = X.mean(0)
        self.sd = X.std(0) + 1e-3
        n = len(X)
        rng = np.random.default_rng(seed)
        positives = max(1.0, float(yb.sum()))
        pw = pos_weight if pos_weight is not None else max(1.0, math.sqrt((len(yb) - positives) / positives))
        adam = {k: [np.zeros_like(getattr(self, k)), np.zeros_like(getattr(self, k))] for k in self.PARAMS}
        t = 0
        history, best, best_state, bad = [], float("inf"), None, 0
        for epoch in range(1, epochs + 1):
            order = rng.permutation(n)
            losses = []
            for s in range(0, n, batch):
                take = order[s:s + batch]
                x, a1, h1, a2, h2, zb, zf = self._forward(X[take])
                y, f = yb[take], yf[take]
                p = _sigmoid(zb)
                w = np.where(y > 0, pw, 1.0)
                loss = float(np.mean(-w * (y * np.log(p + 1e-7) + (1 - y) * np.log(1 - p + 1e-7))))
                dzb = (w * (p - y) / len(take))[:, None]
                q = _softmax(zf)
                has = (f >= 0)
                onehot = np.zeros_like(q)
                onehot[np.arange(len(take))[has], f[has]] = 1
                dzf = (q - onehot) * has[:, None] / max(1, has.sum())
                if has.any():
                    loss += float(-np.mean(np.log(q[has, f[has]] + 1e-7)))
                g = {"Wb": h2.T @ dzb, "bb": dzb.sum(0), "Wf": h2.T @ dzf, "bf": dzf.sum(0)}
                dh2 = dzb @ self.Wb.T + dzf @ self.Wf.T
                da2 = dh2 * (a2 > 0)
                g["W2"], g["b2"] = h1.T @ da2, da2.sum(0)
                da1 = (da2 @ self.W2.T) * (a1 > 0)
                g["W1"], g["b1"] = x.T @ da1, da1.sum(0)
                t += 1
                for k in self.PARAMS:
                    grad = g[k] + (l2 * getattr(self, k) if k.startswith("W") else 0)
                    m, v = adam[k]
                    m[:] = 0.9 * m + 0.1 * grad
                    v[:] = 0.999 * v + 0.001 * grad * grad
                    step = lr * math.sqrt(1 - 0.999 ** t) / (1 - 0.9 ** t)
                    setattr(self, k, (getattr(self, k) - step * m / (np.sqrt(v) + 1e-8)).astype(np.float32))
                losses.append(loss)
            entry = {"epoch": epoch, "train_loss": float(np.mean(losses))}
            if val is not None:
                pv, _ = self.predict(val[0])
                yv = val[1]
                entry["val_loss"] = float(-np.mean(yv * np.log(pv + 1e-7) + (1 - yv) * np.log(1 - pv + 1e-7)))
                if entry["val_loss"] < best - 1e-5:
                    best, bad, best_state = entry["val_loss"], 0, {k: getattr(self, k).copy() for k in self.PARAMS}
                else:
                    bad += 1
            history.append(entry)
            if epoch % 5 == 0 or epoch == 1:
                log(f"  epoch {epoch}: " + ", ".join(f"{k} {v:.4f}" for k, v in entry.items() if k != "epoch"))
            if val is not None and bad >= patience:
                break
        if best_state is not None:
            for k, v in best_state.items():
                setattr(self, k, v)
            history.append({"restored_best_val_loss": best})
        return history

    def save(self, path: str) -> None:
        meta = {**self.meta, "arch": self.ARCH, "dim": self.dim, "hidden": list(self.hidden), "families": list(FAMILIES)}
        buf = io.BytesIO()
        np.savez_compressed(buf, mu=self.mu, sd=self.sd, calibration=self.calibration,
                            meta=np.frombuffer(json.dumps(meta).encode(), np.uint8),
                            **{k: getattr(self, k) for k in self.PARAMS})
        with open(path, "wb") as handle:
            handle.write(buf.getvalue())

    @classmethod
    def load(cls, path: str) -> "IncidentNet":
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(bytes(data["meta"]).decode())
            if meta.get("arch") != cls.ARCH:
                raise ValueError(f"{path}: not an {cls.ARCH} artifact")
            net = cls(meta["dim"], tuple(meta["hidden"]))
            for k in cls.PARAMS + ("mu", "sd", "calibration"):
                setattr(net, k, data[k].astype(np.float32))
            net.meta = meta
        return net


class AlertRanker:
    ARCH = "alert-logit-v1"

    def __init__(self, names: tuple[str, ...]):
        self.names = tuple(names)
        self.w = np.zeros(len(names), np.float32)
        self.b = 0.0
        self.mu = np.zeros(len(names), np.float32)
        self.sd = np.ones(len(names), np.float32)
        self.meta: dict = {}

    def fit(self, X, y, l2: float = 1e-2, iters: int = 3000, lr: float = 0.1) -> None:
        X = np.asarray(X, np.float32)
        y = np.asarray(y, np.float32)
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-3
        Z = (X - self.mu) / self.sd
        pos = max(1.0, y.sum())
        weights = np.where(y > 0, (len(y) - pos) / pos if pos < len(y) / 2 else 1.0, 1.0)
        for _ in range(iters):
            p = _sigmoid(Z @ self.w + self.b)
            g = (p - y) * weights
            self.w -= lr * (Z.T @ g / len(y) + l2 * self.w)
            self.b -= lr * float(g.mean())

    def score(self, x) -> float:
        z = (np.asarray(x, np.float32) - self.mu) / self.sd
        return float(_sigmoid(z @ self.w + self.b))

    def scores(self, X) -> np.ndarray:
        return _sigmoid(((np.asarray(X, np.float32) - self.mu) / self.sd) @ self.w + self.b)

    def why(self, x, k: int = 3) -> list[tuple[str, float]]:
        z = (np.asarray(x, np.float32) - self.mu) / self.sd
        contrib = z * self.w
        order = np.argsort(-np.abs(contrib))[:k]
        return [(self.names[i], float(contrib[i])) for i in order if abs(contrib[i]) > 0.05]

    def to_dict(self) -> dict:
        return {"arch": self.ARCH, "names": list(self.names), "w": [float(x) for x in self.w], "b": self.b,
                "mu": [float(x) for x in self.mu], "sd": [float(x) for x in self.sd], "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict) -> "AlertRanker":
        if d.get("arch") != cls.ARCH:
            raise ValueError("not an alert ranker")
        r = cls(tuple(d["names"]))
        r.w, r.b = np.asarray(d["w"], np.float32), float(d["b"])
        r.mu, r.sd = np.asarray(d["mu"], np.float32), np.asarray(d["sd"], np.float32)
        r.meta = d.get("meta", {})
        return r


# ---------------------------------------------------------------------------------------------- metrics
def auc(y: np.ndarray, s: np.ndarray) -> Optional[float]:
    y = np.asarray(y)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    # average ranks over ties
    allv = np.concatenate([pos, neg])
    _, inv, counts = np.unique(allv, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, weights=ranks)
    ranks = (sums / counts)[inv]
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def average_precision(y: np.ndarray, s: np.ndarray) -> Optional[float]:
    y = np.asarray(y)
    if y.sum() == 0:
        return None
    order = np.argsort(-s)
    hits = y[order]
    precision = np.cumsum(hits) / np.arange(1, len(hits) + 1)
    return float((precision * hits).sum() / hits.sum())


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo)
        if m.any():
            total += m.mean() * abs(float(p[m].mean()) - float(y[m].mean()))
    return total


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def at_threshold(y: np.ndarray, p: np.ndarray, t: float) -> dict:
    pred = p >= t
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    tn = int((~pred & (y == 0)).sum())
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    return {"threshold": t, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": round(precision, 4),
            "precision_ci95": [round(x, 4) for x in wilson(tp, tp + fp)], "recall": round(recall, 4),
            "recall_ci95": [round(x, 4) for x in wilson(tp, tp + fn)],
            "false_positive_rate": round(fp / max(1, fp + tn), 5)}

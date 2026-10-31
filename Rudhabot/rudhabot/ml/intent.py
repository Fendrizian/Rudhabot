"""Clairvoyance intent router: maps what someone types to an existing Rudhabot capability, fast and offline.

    python -m rudhabot.ml.intent          # train, evaluate on the held-out phrasings, save models/intent-v1.*

Model   hashed character n-grams (2-5, word-bounded) + word unigrams/bigrams -> 8,192-dim TF vector (L2-normalised)
        -> 96 ReLU -> softmax over the intents in `intent_data.INTENTS`. Temperature-calibrated on held-out seeds.
Data    developer-written phrases (`intent_data.py`), augmented with politeness, casing and typo variants. The TEST
        phrasings are written separately and never trained on or augmented.
Use     `IntentRouter.route(text)` returns the ranked intents with calibrated confidence. The caller decides:
        confident -> offer that command; unsure -> ask a clarifying question with the top options;
        "chat" -> hand to the OpenAI assistant. The router never runs anything itself.
"""
from __future__ import annotations

import json
import os
import random
import re
import zlib
from dataclasses import dataclass
from typing import Optional

import numpy as np

from rudhabot.ml.intent_data import INTENTS, TEST, TRAIN

DIM = 8192
LABELS = tuple(INTENTS)
_WORD = re.compile(r"[a-z0-9']+")


def normalise(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def featurise(text: str) -> np.ndarray:
    vec = np.zeros(DIM, np.float32)
    clean = normalise(text)
    words = clean.split()
    feats = [f"w:{w}" for w in words] + [f"b:{a}_{b}" for a, b in zip(words, words[1:])]
    for w in words:
        padded = f"<{w}>"
        for n in (2, 3, 4, 5):
            feats += [f"c{n}:{padded[i:i + n]}" for i in range(max(1, len(padded) - n + 1))]
    for f in feats:
        vec[zlib.crc32(f.encode()) % DIM] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


PREFIXES = ("", "", "can you ", "please ", "hey rudhabot ", "yo ", "i want to ", "pls ", "could you ", "let me ")
SUFFIXES = ("", "", "?", " please", " pls", " now", "!!", " thanks")


def _typo(text: str, rng: random.Random) -> str:
    if len(text) < 4:
        return text
    i = rng.randrange(len(text) - 1)
    op = rng.choice(("drop", "swap", "dup"))
    if op == "drop":
        return text[:i] + text[i + 1:]
    if op == "swap":
        return text[:i] + text[i + 1] + text[i] + text[i + 2:]
    return text[:i] + text[i] + text[i:]


def augment(phrase: str, rng: random.Random, n: int = 8) -> list[str]:
    out = [phrase]
    for _ in range(n):
        t = rng.choice(PREFIXES) + phrase + rng.choice(SUFFIXES)
        if rng.random() < 0.35:
            t = _typo(t, rng)
        if rng.random() < 0.3:
            t = t.upper() if rng.random() < 0.2 else t.capitalize()
        out.append(t)
    return out


class IntentNet:
    def __init__(self, hidden: int = 96, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.W1 = (rng.standard_normal((DIM, hidden)) * np.sqrt(2 / 64)).astype(np.float32) * 0.1
        self.b1 = np.zeros(hidden, np.float32)
        self.W2 = (rng.standard_normal((hidden, len(LABELS))) * np.sqrt(2 / hidden)).astype(np.float32)
        self.b2 = np.zeros(len(LABELS), np.float32)
        self.temperature = 1.0

    def logits(self, X):
        h = np.maximum(X @ self.W1 + self.b1, 0)
        return h, h @ self.W2 + self.b2

    def proba(self, X, temperature: Optional[float] = None):
        z = self.logits(X)[1] / (temperature or self.temperature)
        z = z - z.max(1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(1, keepdims=True)

    def fit(self, X, y, epochs=40, lr=3e-3, l2=1e-5, batch=64, seed=0):
        rng = np.random.default_rng(seed)
        params = ("W1", "b1", "W2", "b2")
        m = {k: np.zeros_like(getattr(self, k)) for k in params}
        v = {k: np.zeros_like(getattr(self, k)) for k in params}
        t = 0
        for _ in range(epochs):
            for start in range(0, len(y), batch):
                idx = np.arange(len(y))[start:start + batch]
                xb, yb = X[idx], y[idx]
                h, z = self.logits(xb)
                p = np.exp(z - z.max(1, keepdims=True)); p /= p.sum(1, keepdims=True)
                p[np.arange(len(yb)), yb] -= 1
                dz = p / len(yb)
                g = {"W2": h.T @ dz + l2 * self.W2, "b2": dz.sum(0)}
                dh = (dz @ self.W2.T) * (h > 0)
                g["W1"], g["b1"] = xb.T @ dh + l2 * self.W1, dh.sum(0)
                t += 1
                for k in params:
                    m[k] = 0.9 * m[k] + 0.1 * g[k]
                    v[k] = 0.999 * v[k] + 0.001 * g[k] ** 2
                    setattr(self, k, getattr(self, k) - lr * (m[k] / (1 - 0.9 ** t)) / (np.sqrt(v[k] / (1 - 0.999 ** t)) + 1e-8))
            perm = rng.permutation(len(y))
            X, y = X[perm], y[perm]
        return self

    def calibrate(self, X, y):
        """Pick the temperature that minimises held-out negative log-likelihood (simple grid search)."""
        best = (float("inf"), 1.0)
        for temp in np.linspace(0.3, 4.0, 75):
            p = self.proba(X, temp)
            nll = -np.mean(np.log(p[np.arange(len(y)), y] + 1e-9))
            best = min(best, (nll, float(temp)))
        self.temperature = best[1]
        return self.temperature

    def save(self, path: str, meta: dict) -> None:
        np.savez_compressed(path, W1=self.W1.astype(np.float16), b1=self.b1, W2=self.W2, b2=self.b2,
                            temperature=np.array([self.temperature]), labels=np.array(LABELS),
                            meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8))

    @classmethod
    def load(cls, path: str) -> "IntentNet":
        with np.load(path, allow_pickle=False) as d:
            if tuple(d["labels"]) != LABELS:
                raise ValueError("intent labels changed since this model was trained; retrain with python -m rudhabot.ml.intent")
            net = cls.__new__(cls)
            net.W1, net.b1 = d["W1"].astype(np.float32), d["b1"]
            net.W2, net.b2 = d["W2"], d["b2"]
            net.temperature = float(d["temperature"][0])
        return net


@dataclass(frozen=True)
class Route:
    intent: str
    confidence: float
    alternatives: tuple[tuple[str, float], ...]     # the next best (intent, confidence) pairs

    @property
    def command(self) -> str:
        return INTENTS[self.intent][1]

    @property
    def runnable(self) -> bool:
        return INTENTS[self.intent][2]


class IntentRouter:
    CONFIDENT = 0.60          # act on the top intent (offer it directly)
    CLARIFY = 0.25            # below CONFIDENT but above this: ask which of the top options was meant

    def __init__(self, path: Optional[str] = None):
        self.net: Optional[IntentNet] = None
        self.error: Optional[str] = None
        if path:
            try:
                self.net = IntentNet.load(path)
            except (OSError, ValueError, KeyError) as error:
                self.error = str(error)

    @property
    def ready(self) -> bool:
        return self.net is not None

    def route(self, text: str) -> Optional[Route]:
        if self.net is None or not normalise(text):
            return None
        p = self.net.proba(featurise(text)[None])[0]
        order = np.argsort(-p)
        return Route(LABELS[order[0]], float(p[order[0]]), tuple((LABELS[i], float(p[i])) for i in order[1:4]))


# ------------------------------------------------------------------ training CLI
def _dataset(phrases: dict[str, list[str]], rng: random.Random, aug: int):
    texts, labels = [], []
    for intent, items in phrases.items():
        for phrase in items:
            for t in (augment(phrase, rng, aug) if aug else [phrase]):
                texts.append(t)
                labels.append(LABELS.index(intent))
    return np.stack([featurise(t) for t in texts]), np.array(labels)


def evaluate(net: IntentNet, router_thresholds=(IntentRouter.CONFIDENT,)) -> dict:
    X, y = _dataset(TEST, random.Random(0), 0)
    p = net.proba(X)
    pred = p.argmax(1)
    conf = p.max(1)
    acc = float((pred == y).mean())
    f1s = []
    for k in range(len(LABELS)):
        tp = int(((pred == k) & (y == k)).sum()); fp = int(((pred == k) & (y != k)).sum()); fn = int(((pred != k) & (y == k)).sum())
        if tp + fp + fn:
            f1s.append(2 * tp / (2 * tp + fp + fn))
    bins = np.linspace(0, 1, 11)
    idx = np.clip(np.digitize(conf, bins) - 1, 0, 9)
    ece = float(sum((idx == b).mean() * abs(conf[idx == b].mean() - (pred[idx == b] == y[idx == b]).mean())
                    for b in range(10) if (idx == b).any()))
    selective = {}
    for th in router_thresholds:
        keep = conf >= th
        selective[str(th)] = {"coverage": float(keep.mean()), "accuracy": float((pred[keep] == y[keep]).mean()) if keep.any() else None}
    errors = [(t, LABELS[pi], LABELS[yi]) for t, pi, yi in
              zip([t for k, v in TEST.items() for t in v], pred, y) if pi != yi]
    return {"n": int(len(y)), "accuracy": acc, "macro_f1": float(np.mean(f1s)), "ece": ece,
            "confident_answers": selective, "errors": errors}


def main() -> None:
    rng = random.Random(7)
    seeds = {k: list(v) for k, v in TRAIN.items()}
    held = {k: [] for k in seeds}
    for k, items in seeds.items():                            # hold out ~20% of seed phrases for calibration
        rng.shuffle(items)
        cut = max(1, len(items) // 5)
        held[k], seeds[k] = items[:cut], items[cut:]
    Xtr, ytr = _dataset(seeds, rng, 8)
    Xcal, ycal = _dataset(held, random.Random(1), 4)
    net = IntentNet(seed=3).fit(Xtr, ytr)
    temp = net.calibrate(Xcal, ycal)
    # final model: all training phrases, same recipe, keep the calibrated temperature
    Xall, yall = _dataset(TRAIN, random.Random(9), 8)
    final = IntentNet(seed=3).fit(Xall, yall)
    final.temperature = temp
    report = {"model": "intent-v1", "labels": len(LABELS), "train_examples": int(len(yall)),
              "temperature": temp, "test": evaluate(final),
              "data": "developer-written phrases with augmentation; TEST phrasings written separately, never trained on"}
    os.makedirs("models", exist_ok=True)
    final.save("models/intent-v1.npz", {k: v for k, v in report.items() if k != "test"})
    with open("models/intent-v1.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({k: v for k, v in report["test"].items() if k != "errors"}, indent=2))
    print("errors:", report["test"]["errors"])


if __name__ == "__main__":
    main()

"""Clairvoyance intent router: maps what someone types to an existing Rudhabot capability, fast and offline.

    python -m rudhabot.ml.intent          # train, evaluate on the held-out phrasings, save models/intent-v2.*

Model   hashed character n-grams (2-5, word-bounded) + word unigrams/bigrams -> 8,192-dim TF vector (L2-normalised)
        -> 96 ReLU -> softmax over the intents in `intent_data.INTENTS`. Temperature-calibrated on held-out seeds.
Data    developer-written phrases (`intent_data.py`), augmented with politeness, casing and typo variants. The TEST
        phrasings are written separately and never trained on or augmented.
Use     `IntentRouter.route(text)` returns the ranked intents with calibrated confidence. The caller decides:
        confident (>= the model's `confident` threshold) -> offer that command; unsure -> ask a clarifying
        question with the top options; "chat" -> hand to the OpenAI assistant. The router never runs anything.
Eval    two held-out test sets (TEST, TEST_2) plus grouped 5-fold cross-validation over the training phrases (a
        phrase and its augmented variants never straddle folds). The confident threshold is the lowest one whose
        cross-validated precision reaches TARGET_PRECISION, so offered commands are right that often.
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

from rudhabot.ml.intent_data import INTENTS, TEST, TEST_2, TRAIN

MODEL_FILE = "intent-v2.npz"          # what the bot loads (models/<MODEL_FILE>)
TARGET_PRECISION = 0.90          # a suggestion is a button the user may ignore; v1's fixed 0.60 sat at ~0.91
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
        self.meta: dict = {}

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
            order = rng.permutation(len(y))         # shuffled every epoch, the first included (the data is grouped by intent)
            for start in range(0, len(y), batch):
                idx = order[start:start + batch]
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
            net.meta = json.loads(bytes(d["meta"]).decode()) if "meta" in d and d["meta"].size else {}
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
    CONFIDENT = 0.60          # default: act on the top intent (offer it directly); models carry a measured value
    CLARIFY = 0.25            # below `confident` but above this: ask which of the top options was meant

    def __init__(self, path: Optional[str] = None):
        self.net: Optional[IntentNet] = None
        self.error: Optional[str] = None
        self.confident = self.CONFIDENT
        if path:
            try:
                self.net = IntentNet.load(path)
                self.confident = float(self.net.meta.get("confident_threshold", self.CONFIDENT))
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


HIDDEN, EPOCHS, AUG = 96, 40, 8


def _metrics(p: np.ndarray, y: np.ndarray, threshold: float, texts=None) -> dict:
    """Label accuracy, command accuracy (two intents may run the same command), macro-F1, ECE, selective accuracy
    at `threshold`, and the two costly mistakes: chat offered as a command, and a task sent to chat."""
    pred, conf = p.argmax(1), p.max(1)
    command = lambda i: INTENTS[LABELS[i]][1]
    chat = LABELS.index("chat")
    per_intent, f1s = {}, []
    for k in range(len(LABELS)):
        tp = int(((pred == k) & (y == k)).sum()); fp = int(((pred == k) & (y != k)).sum()); fn = int(((pred != k) & (y == k)).sum())
        if tp + fp + fn:
            f1s.append(2 * tp / (2 * tp + fp + fn))
            per_intent[LABELS[k]] = {"precision": tp / (tp + fp) if tp + fp else None,
                                     "recall": tp / (tp + fn) if tp + fn else None, "support": int((y == k).sum())}
    bins = np.clip(np.digitize(conf, np.linspace(0, 1, 11)) - 1, 0, 9)
    ece = float(sum((bins == b).mean() * abs(conf[bins == b].mean() - (pred[bins == b] == y[bins == b]).mean())
                    for b in range(10) if (bins == b).any()))
    keep = conf >= threshold
    is_chat = y == chat
    out = {"n": int(len(y)), "accuracy": float((pred == y).mean()),
           "command_accuracy": float(np.mean([command(a) == command(b) for a, b in zip(pred, y)])),
           "macro_f1": float(np.mean(f1s)), "ece": ece,
           "confident": {"threshold": threshold, "coverage": float(keep.mean()),
                         "precision": float((pred[keep] == y[keep]).mean()) if keep.any() else None},
           "chat_offered_as_command": float(np.mean((pred[is_chat] != chat) & keep[is_chat])) if is_chat.any() else None,
           "task_sent_to_chat": float(np.mean(pred[~is_chat] == chat)) if (~is_chat).any() else None,
           "per_intent": per_intent}
    if texts is not None:
        out["errors"] = [(t, LABELS[a], LABELS[b], round(float(c), 2)) for t, a, b, c in zip(texts, pred, y, conf) if a != b]
    return out


def evaluate(net: IntentNet, test: dict, threshold: float) -> dict:
    texts = [t for k, v in test.items() if k in LABELS for t in v]
    X, y = _dataset({k: v for k, v in test.items() if k in LABELS}, random.Random(0), 0)
    return _metrics(net.proba(X), y, threshold, texts)


def _train(phrases: dict, seed: int):
    """One model with the shipped recipe, temperature-calibrated on a held-out 20% of the seed phrases."""
    rng = random.Random(seed)
    fit_part, held = {}, {}
    for k, items in phrases.items():
        items = list(items)
        rng.shuffle(items)
        cut = max(1, len(items) // 5) if len(items) > 2 else 0
        held[k], fit_part[k] = items[:cut], items[cut:]
    Xtr, ytr = _dataset(fit_part, rng, AUG)
    held = {k: v for k, v in held.items() if v}
    Xcal, ycal = _dataset(held, random.Random(seed + 1), 4)
    probe = IntentNet(hidden=HIDDEN, seed=3).fit(Xtr, ytr, epochs=EPOCHS)
    temperature = probe.calibrate(Xcal, ycal)
    final = IntentNet(hidden=HIDDEN, seed=3).fit(*_dataset(phrases, random.Random(seed + 2), AUG), epochs=EPOCHS)
    final.temperature = temperature
    return final


def cross_validate(folds: int = 5, seed: int = 0):
    """Out-of-fold probabilities for every training phrase (grouped: a phrase and its variants stay in one fold)."""
    items = [(k, t) for k, v in TRAIN.items() for t in v]
    order = list(range(len(items)))
    random.Random(seed).shuffle(order)
    probs = np.zeros((len(items), len(LABELS)), np.float32)
    for f in range(folds):
        test_idx = set(order[f::folds])
        train = {}
        for i, (k, t) in enumerate(items):
            if i not in test_idx:
                train.setdefault(k, []).append(t)
        net = _train(train, seed + f)
        idx = sorted(test_idx)
        probs[idx] = net.proba(np.stack([featurise(items[i][1]) for i in idx]))
        print(f"  fold {f + 1}/{folds} done")
    y = np.array([LABELS.index(k) for k, _ in items])
    return probs, y, [t for _, t in items]


def pick_threshold(p: np.ndarray, y: np.ndarray, target: float = TARGET_PRECISION) -> float:
    """The lowest confidence threshold whose selective precision reaches `target` (most coverage at that precision)."""
    pred, conf = p.argmax(1), p.max(1)
    for th in np.round(np.arange(0.30, 0.96, 0.01), 2):
        keep = conf >= th
        if keep.any() and (pred[keep] == y[keep]).mean() >= target:
            return float(th)
    return 0.95


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default=MODEL_FILE.removesuffix(".npz"))
    parser.add_argument("--models", default="models")
    args = parser.parse_args()
    print("[cv] grouped 5-fold cross-validation over the training phrases")
    cv_p, cv_y, cv_texts = cross_validate()
    threshold = pick_threshold(cv_p, cv_y)
    print(f"[threshold] confident >= {threshold} (cross-validated precision >= {TARGET_PRECISION})")
    final = _train(TRAIN, seed=7)
    report = {"model": args.version, "labels": len(LABELS), "train_phrases": sum(map(len, TRAIN.values())),
              "train_examples": sum(map(len, TRAIN.values())) * (AUG + 1), "hidden": HIDDEN, "epochs": EPOCHS,
              "temperature": final.temperature, "confident_threshold": threshold, "target_precision": TARGET_PRECISION,
              "cross_validation": _metrics(cv_p, cv_y, threshold),
              "test": evaluate(final, TEST, threshold), "test_2": evaluate(final, TEST_2, threshold),
              "data": "developer-written phrases with augmentation; TEST and TEST_2 phrasings written separately and "
                      "never trained on; TEST_2 was frozen before this version's training data was written"}
    os.makedirs(args.models, exist_ok=True)
    meta = {k: v for k, v in report.items() if k not in ("test", "test_2", "cross_validation")}
    final.save(os.path.join(args.models, f"{args.version}.npz"), meta)
    with open(os.path.join(args.models, f"{args.version}.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    for name in ("cross_validation", "test", "test_2"):
        r = report[name]
        print(f"{name:16s} n={r['n']:4d} acc {r['accuracy']:.3f} cmd-acc {r['command_accuracy']:.3f} "
              f"F1 {r['macro_f1']:.3f} ECE {r['ece']:.3f} | confident: coverage {r['confident']['coverage']:.3f} "
              f"precision {r['confident']['precision']:.3f} | chat->command {r['chat_offered_as_command']:.3f} "
              f"task->chat {r['task_sent_to_chat']:.3f}")
    print("test_2 errors:", report["test_2"]["errors"])


if __name__ == "__main__":
    main()

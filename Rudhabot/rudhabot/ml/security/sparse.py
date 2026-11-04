"""The message-risk network: a hashed-embedding bag feeding a small multi-task MLP, trained with sparse (lazy) Adam.

    x      = sum_j E[bucket_j] / sqrt(n)                 E: 2**18 x 32 embedding table  (8,388,608 parameters)
    h1     = relu(W1 x + b1)                             32 -> 96
    h2     = relu(W2 h1 + b2)                            96 -> 48
    logits = H h2 + c                                    48 -> 3 heads: scam, spam, malicious-url

Only the embedding rows a batch touches are read and updated (gather forward, sorted segment-sum backward, lazy Adam
moments on those rows only), so training stays fast even though the table holds millions of parameters, and a
forward pass for one message touches a few hundred rows (well under a millisecond). Each head is trained only on
examples that have its label (masked binary cross-entropy) and is calibrated afterwards with Platt scaling.
"""
from __future__ import annotations

import io
import json
import math
from typing import Optional

import numpy as np

HEADS = ("scam", "spam", "url")


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


class HashedMultiTaskNet:
    ARCH = "hashed-mtl-v1"

    def __init__(self, buckets: int = 1 << 18, dim: int = 32, hidden: tuple[int, int] = (96, 48), seed: int = 0):
        rng = np.random.default_rng(seed)
        self.buckets, self.dim, self.hidden = buckets, dim, hidden
        h1, h2 = hidden
        # small initial embeddings: a bucket never seen in training then adds almost nothing at inference
        self.E = (rng.standard_normal((buckets, dim)) * 0.005).astype(np.float32)
        self.W1 = (rng.standard_normal((dim, h1)) * math.sqrt(2 / dim)).astype(np.float32)
        self.b1 = np.full(h1, 0.1, np.float32)          # starts every unit active, so gradients reach the table
        self.W2 = (rng.standard_normal((h1, h2)) * math.sqrt(2 / h1)).astype(np.float32)
        self.b2 = np.zeros(h2, np.float32)
        self.H = (rng.standard_normal((h2, len(HEADS))) * math.sqrt(1 / h2)).astype(np.float32)
        self.c = np.zeros(len(HEADS), np.float32)
        self.calibration = np.tile(np.array([1.0, 0.0], np.float32), (len(HEADS), 1))     # Platt (a, b) per head
        self.meta: dict = {}
        self._adam: Optional[dict] = None

    DENSE = ("W1", "b1", "W2", "b2", "H", "c")

    @property
    def params(self) -> int:
        return int(self.E.size + sum(getattr(self, k).size for k in self.DENSE))

    # ------------------------------------------------------------------ forward
    def _pool(self, idx: np.ndarray, off: np.ndarray):
        counts = np.diff(off)
        seg = np.repeat(np.arange(len(counts)), counts)
        scale = (1.0 / np.sqrt(np.maximum(counts, 1))).astype(np.float32)[seg]
        rows = self.E[idx] * scale[:, None]
        if len(idx) == 0:
            return np.zeros((len(counts), self.dim), np.float32), seg, scale
        # every sample has at least one bucket (hashing.encode guarantees it), so CSR offsets are segment starts
        pooled = np.add.reduceat(rows, off[:-1], axis=0)
        return pooled, seg, scale

    def logits(self, idx: np.ndarray, off: np.ndarray, cache: bool = False):
        x, seg, scale = self._pool(idx, off)
        a1 = x @ self.W1 + self.b1
        h1 = np.maximum(a1, 0)
        a2 = h1 @ self.W2 + self.b2
        h2 = np.maximum(a2, 0)
        z = h2 @ self.H + self.c
        if cache:
            return z, (x, seg, scale, a1, h1, a2, h2)
        return z

    def predict(self, idx: np.ndarray, off: np.ndarray) -> np.ndarray:
        """Calibrated probabilities, shape (n, 3) in HEADS order."""
        z = self.logits(idx, off)
        a, b = self.calibration[:, 0], self.calibration[:, 1]
        return _sigmoid(z * a + b)

    # ------------------------------------------------------------------ training
    def _init_adam(self):
        self._adam = {"t": 0, "mE": np.zeros_like(self.E), "vE": np.zeros_like(self.E)}
        for k in self.DENSE:
            self._adam["m" + k] = np.zeros_like(getattr(self, k))
            self._adam["v" + k] = np.zeros_like(getattr(self, k))

    def step(self, idx, off, y, mask, weights, lr: float, l2: float = 1e-5, l2_emb: float = 1e-7) -> float:
        """One Adam step on a batch. y, mask: (n, 3); weights: (n,) per-example weights."""
        if self._adam is None:
            self._init_adam()
        z, (x, seg, scale, a1, h1, a2, h2) = self.logits(idx, off, cache=True)
        p = _sigmoid(z)
        w = mask * weights[:, None]
        denom = max(1.0, float(w.sum()))
        loss = float(-(w * (y * np.log(p + 1e-7) + (1 - y) * np.log(1 - p + 1e-7))).sum() / denom)
        dz = (p - y) * w / denom
        grads = {"H": h2.T @ dz, "c": dz.sum(0)}
        dh2 = dz @ self.H.T
        da2 = dh2 * (a2 > 0)
        grads["W2"], grads["b2"] = h1.T @ da2, da2.sum(0)
        dh1 = da2 @ self.W2.T
        da1 = dh1 * (a1 > 0)
        grads["W1"], grads["b1"] = x.T @ da1, da1.sum(0)
        dx = da1 @ self.W1.T                                          # (n, dim)
        drows = dx[seg] * scale[:, None]                              # gradient for each gathered row
        order = np.argsort(idx, kind="stable")
        sorted_idx = idx[order]
        starts = np.flatnonzero(np.r_[True, sorted_idx[1:] != sorted_idx[:-1]])
        rows = sorted_idx[starts]
        gE = np.add.reduceat(drows[order], starts, axis=0)
        A = self._adam
        A["t"] += 1
        t, b1_, b2_, eps = A["t"], 0.9, 0.999, 1e-8
        corr = math.sqrt(1 - b2_ ** t) / (1 - b1_ ** t)
        for k in self.DENSE:
            g = grads[k] + (l2 * getattr(self, k) if k in ("W1", "W2", "H") else 0)
            A["m" + k] = b1_ * A["m" + k] + (1 - b1_) * g
            A["v" + k] = b2_ * A["v" + k] + (1 - b2_) * g * g
            setattr(self, k, (getattr(self, k) - lr * corr * A["m" + k] / (np.sqrt(A["v" + k]) + eps)).astype(np.float32))
        gE = gE + l2_emb * self.E[rows]
        mE = A["mE"][rows] = b1_ * A["mE"][rows] + (1 - b1_) * gE     # lazy Adam: only rows in this batch
        vE = A["vE"][rows] = b2_ * A["vE"][rows] + (1 - b2_) * gE * gE
        self.E[rows] -= (lr * corr * mE / (np.sqrt(vE) + eps)).astype(np.float32)
        return loss

    def fit(self, data, *, epochs: int = 6, batch: int = 256, lr: float = 1e-3, seed: int = 0, val=None,
            patience: int = 2, token_dropout: float = 0.0, log=print) -> list[dict]:
        """data/val: (idx, off, y, mask, weights). Keeps the weights of the best validation epoch.

        token_dropout: drop this share of each training example's buckets per step (at least one is kept), so the
        network can't lean on any single memorised token: the main defence against overfitting a table this big."""
        idx, off, y, mask, weights = data
        n = len(off) - 1
        rng = np.random.default_rng(seed)
        history, best, best_state, bad = [], float("inf"), None, 0
        steps = epochs * math.ceil(n / batch)
        step = 0
        for epoch in range(1, epochs + 1):
            order = rng.permutation(n)
            losses = []
            for start in range(0, n, batch):
                take = order[start:start + batch]
                b_idx, b_off = _gather(idx, off, take)
                if token_dropout > 0:
                    b_idx, b_off = _drop_tokens(b_idx, b_off, token_dropout, rng)
                rate = lr * 0.5 * (1 + math.cos(math.pi * step / max(1, steps)))     # cosine schedule
                losses.append(self.step(b_idx, b_off, y[take], mask[take], weights[take], rate))
                step += 1
            entry = {"epoch": epoch, "train_loss": float(np.mean(losses))}
            if val is not None:
                entry["val_loss"] = self.loss(val)
                if entry["val_loss"] < best - 1e-5:
                    best, bad = entry["val_loss"], 0
                    best_state = self._state()
                else:
                    bad += 1
            history.append(entry)
            log(f"  epoch {epoch}: " + ", ".join(f"{k} {v:.4f}" for k, v in entry.items() if k != "epoch"))
            if val is not None and bad >= patience:
                break
        if best_state is not None:
            self._load_state(best_state)
            history.append({"restored_best_val_loss": best})
        self._adam = None
        return history

    def loss(self, data, chunk: int = 4096) -> float:
        idx, off, y, mask, weights = data
        total, denom = 0.0, 0.0
        for s in range(0, len(off) - 1, chunk):
            take = np.arange(s, min(len(off) - 1, s + chunk))
            b_idx, b_off = _gather(idx, off, take)
            p = _sigmoid(self.logits(b_idx, b_off))
            w = mask[take] * weights[take, None]
            total += float(-(w * (y[take] * np.log(p + 1e-7) + (1 - y[take]) * np.log(1 - p + 1e-7))).sum())
            denom += float(w.sum())
        return total / max(1.0, denom)

    def raw_logits(self, idx, off, chunk: int = 4096) -> np.ndarray:
        out = []
        for s in range(0, len(off) - 1, chunk):
            take = np.arange(s, min(len(off) - 1, s + chunk))
            out.append(self.logits(*_gather(idx, off, take)))
        return np.concatenate(out) if out else np.zeros((0, len(HEADS)), np.float32)

    # ------------------------------------------------------------------ persistence
    def _state(self) -> dict:
        return {k: getattr(self, k).copy() for k in ("E",) + self.DENSE}

    def _load_state(self, state: dict) -> None:
        for k, v in state.items():
            setattr(self, k, v)

    def save(self, path: str) -> None:
        arrays = {k: getattr(self, k) for k in ("E",) + self.DENSE}
        arrays["E"] = arrays["E"].astype(np.float16)          # halves the file; float16 is plenty for embeddings
        meta = {**self.meta, "arch": self.ARCH, "buckets": self.buckets, "dim": self.dim, "hidden": list(self.hidden),
                "heads": list(HEADS)}
        buf = io.BytesIO()
        np.savez_compressed(buf, calibration=self.calibration, meta=np.frombuffer(json.dumps(meta).encode(), np.uint8),
                            **arrays)
        with open(path, "wb") as handle:
            handle.write(buf.getvalue())

    @classmethod
    def load(cls, path: str) -> "HashedMultiTaskNet":
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(bytes(data["meta"]).decode())
            if meta.get("arch") != cls.ARCH:
                raise ValueError(f"{path}: architecture {meta.get('arch')!r} is not {cls.ARCH}")
            net = cls(meta["buckets"], meta["dim"], tuple(meta["hidden"]))
            net.E = data["E"].astype(np.float32)
            for k in cls.DENSE:
                setattr(net, k, data[k].astype(np.float32))
            net.calibration = data["calibration"].astype(np.float32)
            net.meta = meta
        return net


def _gather(idx: np.ndarray, off: np.ndarray, take: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    starts, ends = off[take], off[take + 1]
    lengths = ends - starts
    new_off = np.zeros(len(take) + 1, np.int64)
    np.cumsum(lengths, out=new_off[1:])
    if len(take) == 0:
        return idx[:0], new_off
    positions = np.repeat(starts - new_off[:-1], lengths) + np.arange(new_off[-1])
    return idx[positions], new_off


def _drop_tokens(idx: np.ndarray, off: np.ndarray, rate: float, rng) -> tuple[np.ndarray, np.ndarray]:
    keep = rng.random(len(idx)) >= rate
    keep[off[:-1]] |= ~np.add.reduceat(keep, off[:-1]).astype(bool)      # every example keeps its first bucket at least
    counts = np.add.reduceat(keep.astype(np.int64), off[:-1])
    new_off = np.zeros(len(off), np.int64)
    np.cumsum(counts, out=new_off[1:])
    return idx[keep], new_off


def fit_platt(logits: np.ndarray, y: np.ndarray, iters: int = 300, lr: float = 0.05) -> tuple[float, float]:
    """Platt scaling: p = sigmoid(a*z + b), fitted by gradient descent on log-loss."""
    a, b = 1.0, 0.0
    for _ in range(iters):
        p = _sigmoid(a * logits + b)
        g = p - y
        a -= lr * float(np.mean(g * logits))
        b -= lr * float(np.mean(g))
    return a, b

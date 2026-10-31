"""OracleNet: a compact win-probability network, implemented in numpy (no deep-learning framework needed to train or serve).

    pooled_k = bags_k @ E                (k = own hand, own board, enemy board; E is a learned card embedding)
    x  = [dense, pooled_hand, pooled_mine, pooled_theirs]
    h1 = relu(x W1 + b1);  h2 = relu(h1 W2 + b2);  P(win) = sigmoid(h2 w3 + b3)

Trained with binary cross-entropy (draws are 0.5 targets), L2 weight decay and Adam. ~50k parameters, ~0.2 MB.
"""
from __future__ import annotations

import io
import json
from typing import Optional

import numpy as np


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


class OracleNet:
    PARAMS = ("E", "W1", "b1", "W2", "b2", "w3", "b3")

    def __init__(self, dense_dim: int, n_cards: int, embed: int = 16, h1: int = 128, h2: int = 64, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.dense_dim, self.n_cards, self.embed = dense_dim, n_cards, embed
        fan_in = dense_dim + 3 * embed
        self.E = (rng.standard_normal((n_cards, embed)) * 0.1).astype(np.float32)
        self.W1 = (rng.standard_normal((fan_in, h1)) * np.sqrt(2 / fan_in)).astype(np.float32)
        self.b1 = np.zeros(h1, np.float32)
        self.W2 = (rng.standard_normal((h1, h2)) * np.sqrt(2 / h1)).astype(np.float32)
        self.b2 = np.zeros(h2, np.float32)
        self.w3 = (rng.standard_normal((h2, 1)) * np.sqrt(1 / h2)).astype(np.float32)
        self.b3 = np.zeros(1, np.float32)
        self.meta: dict = {}
        self.calibration = (1.0, 0.0)          # Platt scaling (a, b) applied to the logit; fitted on held-out games
        self._adam: Optional[dict] = None

    # ------------------------------------------------------------------ inference
    def _forward(self, dense: np.ndarray, bags: np.ndarray):
        pooled = np.einsum("bkc,ce->bke", bags, self.E).reshape(len(dense), -1)
        x = np.concatenate([dense, pooled], axis=1)
        a1 = x @ self.W1 + self.b1
        h1 = np.maximum(a1, 0)
        a2 = h1 @ self.W2 + self.b2
        h2 = np.maximum(a2, 0)
        z = (h2 @ self.w3 + self.b3)[:, 0]
        return x, h1, h2, z

    def logits(self, dense: np.ndarray, bags: np.ndarray) -> np.ndarray:
        return self._forward(dense.astype(np.float32, copy=False), bags.astype(np.float32, copy=False))[3]

    def predict(self, dense: np.ndarray, bags: np.ndarray) -> np.ndarray:
        """Calibrated P(win) for each row. `dense` [B, D], `bags` [B, 3, C]."""
        a, b = self.calibration
        return _sigmoid(a * self.logits(dense, bags) + b)

    def calibrate(self, dense: np.ndarray, bags: np.ndarray, y: np.ndarray, iters: int = 50) -> tuple[float, float]:
        """Fit Platt scaling by Newton's method on held-out data (never on the test set)."""
        z = self.logits(dense, bags).astype(np.float64)
        y = y.astype(np.float64)
        a, b = 1.0, 0.0
        for _ in range(iters):
            p = 1 / (1 + np.exp(-(a * z + b)))
            w = np.maximum(p * (1 - p), 1e-9)
            ga, gb = np.sum((p - y) * z), np.sum(p - y)
            haa, hab, hbb = np.sum(w * z * z) + 1e-6, np.sum(w * z), np.sum(w) + 1e-6
            det = haa * hbb - hab * hab
            if det <= 0:
                break
            da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
            a, b = a - da, b - db
            if abs(da) + abs(db) < 1e-8:
                break
        self.calibration = (float(a), float(b))
        return self.calibration

    # ------------------------------------------------------------------ training
    def _grads(self, dense, bags, y, l2):
        x, h1, h2, z = self._forward(dense, bags)
        p = _sigmoid(z)
        n = len(y)
        eps = 1e-7
        loss = float(-np.mean(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)))
        dz = ((p - y) / n).astype(np.float32)[:, None]
        g = {"w3": h2.T @ dz, "b3": dz.sum(0)}
        dh2 = (dz @ self.w3.T) * (h2 > 0)
        g["W2"], g["b2"] = h1.T @ dh2, dh2.sum(0)
        dh1 = (dh2 @ self.W2.T) * (h1 > 0)
        g["W1"], g["b1"] = x.T @ dh1, dh1.sum(0)
        dx = dh1 @ self.W1.T
        dpooled = dx[:, self.dense_dim:].reshape(n, 3, self.embed)
        g["E"] = np.einsum("bkc,bke->ce", bags, dpooled)
        for name in ("W1", "W2", "w3", "E"):
            g[name] = g[name] + l2 * getattr(self, name)
        return loss, g

    def fit(self, dense, bags, y, *, epochs=8, batch=512, lr=2e-3, l2=1e-4, seed=0, val=None, patience=2,
            log=print) -> list[dict]:
        """Mini-batch Adam with a cosine learning-rate schedule. With `val`, keeps the weights of the best validation
        epoch and stops after `patience` epochs without improvement (states from one game are highly correlated, so
        the network overfits quickly)."""
        rng = np.random.default_rng(seed)
        y = y.astype(np.float32)
        if self._adam is None:
            self._adam = {k: [np.zeros_like(getattr(self, k)), np.zeros_like(getattr(self, k))] for k in self.PARAMS}
            self._adam["t"] = 0
        history = []
        best, best_params, stale = float("inf"), None, 0
        b1, b2, eps = 0.9, 0.999, 1e-8
        for epoch in range(epochs):
            order = rng.permutation(len(y))
            losses = []
            step_lr = lr * (0.5 * (1 + np.cos(np.pi * epoch / max(1, epochs))))       # cosine schedule
            for start in range(0, len(y), batch):
                idx = order[start:start + batch]
                loss, grads = self._grads(dense[idx], bags[idx], y[idx], l2)
                losses.append(loss)
                self._adam["t"] += 1
                t = self._adam["t"]
                for k in self.PARAMS:
                    m, v = self._adam[k]
                    m *= b1; m += (1 - b1) * grads[k]
                    v *= b2; v += (1 - b2) * grads[k] ** 2
                    mhat, vhat = m / (1 - b1 ** t), v / (1 - b2 ** t)
                    setattr(self, k, (getattr(self, k) - step_lr * mhat / (np.sqrt(vhat) + eps)).astype(np.float32))
            row = {"epoch": epoch + 1, "train_loss": float(np.mean(losses))}
            if val is not None:
                pv = self.predict(val[0], val[1])
                yv = val[2]
                row["val_loss"] = float(-np.mean(yv * np.log(pv + 1e-7) + (1 - yv) * np.log(1 - pv + 1e-7)))
            history.append(row)
            log(f"  epoch {row['epoch']}: " + ", ".join(f"{k}={v:.4f}" for k, v in row.items() if k != "epoch"))
            if val is not None:
                if row["val_loss"] < best - 1e-4:
                    best, stale = row["val_loss"], 0
                    best_params = {k: getattr(self, k).copy() for k in self.PARAMS}
                else:
                    stale += 1
                    if stale >= patience:
                        break
        if best_params is not None:
            for k, value in best_params.items():
                setattr(self, k, value)
            history.append({"restored_best_val_loss": best})
        return history

    # ------------------------------------------------------------------ artifacts
    def card_embeddings(self) -> np.ndarray:
        norms = np.linalg.norm(self.E, axis=1, keepdims=True)
        return self.E / np.maximum(norms, 1e-8)

    def save(self, path: str) -> None:
        arrays = {k: getattr(self, k) for k in self.PARAMS}
        dims = np.array([self.dense_dim, self.n_cards, self.embed], dtype=np.int64)
        np.savez_compressed(path, dims=dims, calibration=np.array(self.calibration, dtype=np.float64), meta=np.frombuffer(json.dumps(self.meta).encode(), dtype=np.uint8), **arrays)

    @classmethod
    def load(cls, path_or_bytes) -> "OracleNet":
        source = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes
        with np.load(source, allow_pickle=False) as data:
            dense_dim, n_cards, embed = (int(v) for v in data["dims"])
            net = cls(dense_dim, n_cards, embed, h1=data["W1"].shape[1], h2=data["W2"].shape[1])
            for k in cls.PARAMS:
                setattr(net, k, data[k].astype(np.float32))
            net.meta = json.loads(bytes(data["meta"]).decode()) if data["meta"].size else {}
            if "calibration" in data:
                net.calibration = tuple(float(v) for v in data["calibration"])
        return net

    def n_params(self) -> int:
        return int(sum(getattr(self, k).size for k in self.PARAMS))

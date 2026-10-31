"""Clairvoyance's networks, in pure numpy (no deep-learning framework needed to train or serve).

v1  ClairvoyanceNet      dense features + pooled card bags            -> MLP -> P(win)          (~46k params)
v2  ClairvoyanceSlotNet  per-slot encoder shared by all 8 field slots:
        slot_k  = relu([attrs_k, E[unit_k], att_k @ E] Ws + bs)        (unit identity + attachments)
        x       = [glob, slot_1..slot_8 (positional), hand@E, mine@E, theirs@E]
        P(win)  = sigmoid(MLP(x))                                                               (~180k params)

Both share one training loop: binary cross-entropy (soft targets allowed), L2, Adam with a cosine schedule, early stopping
on validation loss, and Platt calibration fitted on held-out games. Artifacts are .npz files carrying the architecture
name, so `load_net()` restores the right class.
"""
from __future__ import annotations

import io
import json
from typing import Optional, Sequence

import numpy as np


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


class _Net:
    ARCH = "base"
    PARAMS: tuple[str, ...] = ()

    def __init__(self):
        self.meta: dict = {}
        self.calibration = (1.0, 0.0)          # Platt scaling (a, b) applied to the logit; fitted on held-out games
        self._adam: Optional[dict] = None

    # ------------------------------------------------------------------ interface for subclasses
    def logits(self, *inputs) -> np.ndarray:
        raise NotImplementedError

    def _grads(self, inputs: Sequence[np.ndarray], y: np.ndarray, l2: float):
        raise NotImplementedError

    def _dims(self) -> dict:
        raise NotImplementedError

    # ------------------------------------------------------------------ shared behaviour
    def predict(self, *inputs) -> np.ndarray:
        """Calibrated P(win) for each row."""
        a, b = self.calibration
        return _sigmoid(a * self.logits(*inputs) + b)

    @staticmethod
    def _bce(p, y):
        return float(-np.mean(y * np.log(p + 1e-7) + (1 - y) * np.log(1 - p + 1e-7)))

    def fit(self, inputs: Sequence[np.ndarray], y, *, epochs=8, batch=512, lr=2e-3, l2=1e-4, seed=0, val=None,
            patience=2, log=print) -> list[dict]:
        """Mini-batch Adam with a cosine learning-rate schedule. With `val=(inputs, y)`, keeps the weights of the best
        validation epoch and stops after `patience` epochs without improvement (states from one game are highly
        correlated, so these networks overfit quickly)."""
        rng = np.random.default_rng(seed)
        y = np.asarray(y, dtype=np.float32)
        if self._adam is None:
            self._adam = {k: [np.zeros_like(getattr(self, k)), np.zeros_like(getattr(self, k))] for k in self.PARAMS}
            self._adam["t"] = 0
        history, best, best_params, stale = [], float("inf"), None, 0
        b1, b2, eps = 0.9, 0.999, 1e-8
        for epoch in range(epochs):
            order = rng.permutation(len(y))
            losses = []
            step_lr = lr * (0.5 * (1 + np.cos(np.pi * epoch / max(1, epochs))))
            for start in range(0, len(y), batch):
                idx = order[start:start + batch]
                loss, grads = self._grads([x[idx] for x in inputs], y[idx], l2)
                losses.append(loss)
                self._adam["t"] += 1
                t = self._adam["t"]
                for k in self.PARAMS:
                    m, v = self._adam[k]
                    m *= b1; m += (1 - b1) * grads[k]
                    v *= b2; v += (1 - b2) * grads[k] ** 2
                    setattr(self, k, (getattr(self, k) - step_lr * (m / (1 - b1 ** t)) / (np.sqrt(v / (1 - b2 ** t)) + eps))
                            .astype(np.float32))
            row = {"epoch": epoch + 1, "train_loss": float(np.mean(losses))}
            if val is not None:
                row["val_loss"] = self._bce(self.predict(*val[0]), np.asarray(val[1], dtype=np.float32))
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

    def calibrate(self, inputs: Sequence[np.ndarray], y: np.ndarray, iters: int = 50) -> tuple[float, float]:
        """Fit Platt scaling by Newton's method on held-out data (never on the test set)."""
        z = self.logits(*inputs).astype(np.float64)
        y = np.asarray(y, dtype=np.float64)
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

    def card_embeddings(self) -> np.ndarray:
        norms = np.linalg.norm(self.E, axis=1, keepdims=True)
        return self.E / np.maximum(norms, 1e-8)

    def n_params(self) -> int:
        return int(sum(getattr(self, k).size for k in self.PARAMS))

    def save(self, path: str) -> None:
        arrays = {k: getattr(self, k) for k in self.PARAMS}
        header = {"arch": self.ARCH, "dims": self._dims()}
        np.savez_compressed(path, header=np.frombuffer(json.dumps(header).encode(), dtype=np.uint8),
                            calibration=np.array(self.calibration, dtype=np.float64),
                            meta=np.frombuffer(json.dumps(self.meta).encode(), dtype=np.uint8), **arrays)


class ClairvoyanceNet(_Net):
    """v1: [dense, pooled_hand, pooled_mine, pooled_theirs] -> 128 -> 64 -> 1."""
    ARCH = "v1"
    PARAMS = ("E", "W1", "b1", "W2", "b2", "w3", "b3")

    def __init__(self, dense_dim: int, n_cards: int, embed: int = 16, h1: int = 128, h2: int = 64, seed: int = 0):
        super().__init__()
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

    def _dims(self):
        return {"dense_dim": self.dense_dim, "n_cards": self.n_cards, "embed": self.embed,
                "h1": self.W1.shape[1], "h2": self.W2.shape[1]}

    def _forward(self, dense, bags):
        pooled = np.einsum("bkc,ce->bke", bags, self.E).reshape(len(dense), -1)
        x = np.concatenate([dense, pooled], axis=1)
        h1 = np.maximum(x @ self.W1 + self.b1, 0)
        h2 = np.maximum(h1 @ self.W2 + self.b2, 0)
        return x, h1, h2, (h2 @ self.w3 + self.b3)[:, 0]

    def logits(self, dense, bags):
        return self._forward(np.asarray(dense, np.float32), np.asarray(bags, np.float32))[3]

    def _grads(self, inputs, y, l2):
        dense, bags = (np.asarray(a, np.float32) for a in inputs)
        x, h1, h2, z = self._forward(dense, bags)
        p = _sigmoid(z)
        n = len(y)
        loss = self._bce(p, y)
        dz = ((p - y) / n).astype(np.float32)[:, None]
        g = {"w3": h2.T @ dz, "b3": dz.sum(0)}
        dh2 = (dz @ self.w3.T) * (h2 > 0)
        g["W2"], g["b2"] = h1.T @ dh2, dh2.sum(0)
        dh1 = (dh2 @ self.W2.T) * (h1 > 0)
        g["W1"], g["b1"] = x.T @ dh1, dh1.sum(0)
        dpooled = (dh1 @ self.W1.T)[:, self.dense_dim:].reshape(n, 3, self.embed)
        g["E"] = np.einsum("bkc,bke->ce", bags, dpooled)
        for name in ("W1", "W2", "w3", "E"):
            g[name] = g[name] + l2 * getattr(self, name)
        return loss, g


class ClairvoyanceSlotNet(_Net):
    """v2: a slot encoder shared by the 8 field slots (unit identity + attachments), then a 256-128 trunk."""
    ARCH = "v2"
    PARAMS = ("E", "Ws", "bs", "W1", "b1", "W2", "b2", "w3", "b3")
    SLOTS = 8

    def __init__(self, glob_dim: int, slot_dim: int, n_cards: int, embed: int = 24, slot_hidden: int = 48,
                 h1: int = 256, h2: int = 128, seed: int = 0):
        super().__init__()
        rng = np.random.default_rng(seed)
        self.glob_dim, self.slot_dim, self.n_cards, self.embed, self.slot_hidden = glob_dim, slot_dim, n_cards, embed, slot_hidden
        s_in = slot_dim + 2 * embed
        fan_in = glob_dim + self.SLOTS * slot_hidden + 3 * embed
        self.E = (rng.standard_normal((n_cards, embed)) * 0.1).astype(np.float32)
        self.Ws = (rng.standard_normal((s_in, slot_hidden)) * np.sqrt(2 / s_in)).astype(np.float32)
        self.bs = np.zeros(slot_hidden, np.float32)
        self.W1 = (rng.standard_normal((fan_in, h1)) * np.sqrt(2 / fan_in)).astype(np.float32)
        self.b1 = np.zeros(h1, np.float32)
        self.W2 = (rng.standard_normal((h1, h2)) * np.sqrt(2 / h1)).astype(np.float32)
        self.b2 = np.zeros(h2, np.float32)
        self.w3 = (rng.standard_normal((h2, 1)) * np.sqrt(1 / h2)).astype(np.float32)
        self.b3 = np.zeros(1, np.float32)

    def _dims(self):
        return {"glob_dim": self.glob_dim, "slot_dim": self.slot_dim, "n_cards": self.n_cards, "embed": self.embed,
                "slot_hidden": self.slot_hidden, "h1": self.W1.shape[1], "h2": self.W2.shape[1]}

    def _unit_emb(self, cids):
        padded = np.vstack([self.E, np.zeros((1, self.embed), np.float32)])
        return padded[np.where(cids >= 0, cids, self.n_cards)]

    def _forward(self, glob, slots, cids, att, bags):
        n = len(glob)
        xs = np.concatenate([slots, self._unit_emb(cids), att @ self.E], axis=2)          # [B, 8, s_in]
        hs = np.maximum(xs @ self.Ws + self.bs, 0)                                          # [B, 8, H]
        pooled = np.einsum("bkc,ce->bke", bags, self.E).reshape(n, -1)
        x = np.concatenate([glob, hs.reshape(n, -1), pooled], axis=1)
        h1 = np.maximum(x @ self.W1 + self.b1, 0)
        h2 = np.maximum(h1 @ self.W2 + self.b2, 0)
        return xs, hs, x, h1, h2, (h2 @ self.w3 + self.b3)[:, 0]

    @staticmethod
    def _cast(inputs):
        glob, slots, cids, att, bags = inputs
        return (np.asarray(glob, np.float32), np.asarray(slots, np.float32), np.asarray(cids, np.int64),
                np.asarray(att, np.float32), np.asarray(bags, np.float32))

    def logits(self, *inputs):
        return self._forward(*self._cast(inputs))[5]

    def _grads(self, inputs, y, l2):
        glob, slots, cids, att, bags = self._cast(inputs)
        xs, hs, x, h1, h2, z = self._forward(glob, slots, cids, att, bags)
        p = _sigmoid(z)
        n = len(y)
        loss = self._bce(p, y)
        dz = ((p - y) / n).astype(np.float32)[:, None]
        g = {"w3": h2.T @ dz, "b3": dz.sum(0)}
        dh2 = (dz @ self.w3.T) * (h2 > 0)
        g["W2"], g["b2"] = h1.T @ dh2, dh2.sum(0)
        dh1 = (dh2 @ self.W2.T) * (h1 > 0)
        g["W1"], g["b1"] = x.T @ dh1, dh1.sum(0)
        dx = dh1 @ self.W1.T
        H, e = self.slot_hidden, self.embed
        start = self.glob_dim
        dhs = dx[:, start:start + self.SLOTS * H].reshape(n, self.SLOTS, H) * (hs > 0)
        dpooled = dx[:, start + self.SLOTS * H:].reshape(n, 3, e)
        g["Ws"] = np.einsum("bki,bkh->ih", xs, dhs)
        g["bs"] = dhs.sum((0, 1))
        dxs = dhs @ self.Ws.T
        d_unit = dxs[:, :, self.slot_dim:self.slot_dim + e]
        d_att = dxs[:, :, self.slot_dim + e:]
        dE = np.einsum("bkc,bke->ce", bags, dpooled) + np.einsum("bkc,bke->ce", att, d_att)
        dE_pad = np.zeros((self.n_cards + 1, e), np.float32)
        np.add.at(dE_pad, np.where(cids >= 0, cids, self.n_cards).reshape(-1), d_unit.reshape(-1, e))
        g["E"] = dE + dE_pad[:-1]
        for name in ("W1", "W2", "w3", "E", "Ws"):
            g[name] = g[name] + l2 * getattr(self, name)
        return loss, g


ARCHES = {"v1": ClairvoyanceNet, "v2": ClairvoyanceSlotNet}


def load_net(path_or_bytes):
    """Restore any Clairvoyance network. Also reads v1 artifacts written before the header existed."""
    source = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes
    with np.load(source, allow_pickle=False) as data:
        if "header" in data:
            header = json.loads(bytes(data["header"]).decode())
            arch, dims = header["arch"], header["dims"]
        else:                                                     # legacy v1 (oracle-v1)
            dense_dim, n_cards, embed = (int(v) for v in data["dims"])
            arch, dims = "v1", {"dense_dim": dense_dim, "n_cards": n_cards, "embed": embed,
                                "h1": data["W1"].shape[1], "h2": data["W2"].shape[1]}
        net = ARCHES[arch](**dims)
        for k in net.PARAMS:
            setattr(net, k, data[k].astype(np.float32))
        net.meta = json.loads(bytes(data["meta"]).decode()) if data["meta"].size else {}
        if "calibration" in data:
            net.calibration = tuple(float(v) for v in data["calibration"])
    return net

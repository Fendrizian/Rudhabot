"""Text normalisation and fingerprints: what lets detectors compare messages without storing them.

normalise()      lower-case, accents and zero-width characters removed, look-alike letters folded, whitespace collapsed
skeleton()       normalise() plus leetspeak folded (0->o, 1->l, 3->e, ...), for look-alike detection
content_hash()   a short hash of the normalised text: equal for exact (and trivially varied) repeats
MinHasher        64-permutation MinHash over character 5-gram shingles: similar texts get similar signatures
CoordinationIndex  locality-sensitive hashing over recent signatures, to find near-duplicate messages posted by
                 *different* accounts within a time window (coordinated spam), keeping only signatures in memory
name_shape()     "Aa9" style shape of a username, for spotting a batch of generated accounts
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

import numpy as np

ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­᠎‪‫‬‭‮⁦⁧⁨⁩"))
BIDI_OVERRIDES = "‪‫‬‭‮⁦⁧⁨⁩"

# letters from other scripts that render like Latin ones (the common subset of Unicode's confusables list)
_CONFUSABLE = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "һ": "h",
    "ԁ": "d", "ɡ": "g", "ո": "n", "ս": "u", "ν": "v", "ο": "o", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ρ": "p",
    "τ": "t", "υ": "u", "χ": "x", "ϲ": "c", "ԛ": "q", "ԝ": "w", "ɩ": "i", "ı": "i", "ⅼ": "l", "ℓ": "l", "ｍ": "m",
    "ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "đ": "d", "ł": "l", "ħ": "h",
}
_LEET = {"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "!": "i", "|": "l"}
_SPACE = re.compile(r"\s+")


def strip_invisible(text: str) -> str:
    return text.translate(ZERO_WIDTH)


def has_bidi_override(text: str) -> bool:
    return any(ch in text for ch in BIDI_OVERRIDES)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", strip_invisible(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = "".join(_CONFUSABLE.get(ch, ch) for ch in text.lower())
    return _SPACE.sub(" ", text).strip()


def skeleton(text: str) -> str:
    return "".join(_LEET.get(ch, ch) for ch in normalise(text))


def content_hash(text: str) -> str:
    """Repeats with different spacing, case, punctuation or invisible characters hash the same."""
    core = re.sub(r"[^\w]+", "", normalise(text))
    return hashlib.blake2b(core.encode("utf-8"), digest_size=8).hexdigest()


def shingles(text: str, k: int = 5) -> set[str]:
    core = normalise(text)
    if len(core) <= k:
        return {core} if core else set()
    return {core[i:i + k] for i in range(len(core) - k + 1)}


def name_shape(name: str) -> str:
    """'John_Doe1234' -> 'Aa_Aa9'. Generated account batches tend to share one shape."""
    out = []
    for ch in strip_invisible(name):
        cls = "A" if ch.isupper() else "a" if ch.isalpha() else "9" if ch.isdigit() else "_"
        if not out or out[-1] != cls:
            out.append(cls)
    return "".join(out)[:16]


def stable_hash32(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(), "little")


class MinHasher:
    """MinHash with `n` universal hash functions h(x) = (a*x + b) mod p over 32-bit shingle hashes (p = 2^31 - 1)."""
    P = (1 << 31) - 1

    def __init__(self, n: int = 64, seed: int = 0x5EC):
        rng = np.random.default_rng(seed)
        self.n = n
        self.a = rng.integers(1, self.P, size=n, dtype=np.int64)
        self.b = rng.integers(0, self.P, size=n, dtype=np.int64)

    def signature(self, text: str) -> Optional[np.ndarray]:
        grams = shingles(text)
        if not grams:
            return None
        x = np.fromiter((stable_hash32(g) % self.P for g in grams), dtype=np.int64, count=len(grams))
        return ((self.a[:, None] * x[None, :] + self.b[:, None]) % self.P).min(axis=1)

    @staticmethod
    def similarity(sig_a: np.ndarray, sig_b: np.ndarray) -> float:
        """Estimated Jaccard similarity of the two shingle sets."""
        return float(np.mean(sig_a == sig_b))


@dataclass
class _Entry:
    ts: float
    author: int
    message_id: int
    channel_id: int
    signature: np.ndarray


@dataclass
class Cluster:
    """Near-duplicate messages from several accounts."""
    authors: set[int] = field(default_factory=set)
    message_ids: list[tuple[int, int]] = field(default_factory=list)      # (channel_id, message_id)
    similarity: float = 0.0
    fingerprint: str = ""


class CoordinationIndex:
    """LSH over recent MinHash signatures: 16 bands x 4 rows finds pairs above ~0.5 Jaccard with high probability;
    each candidate is then verified against `min_similarity`. Only signatures are kept, for `window_s` seconds."""

    MAX_CANDIDATES = 256        # verify at most this many candidates per message (the most recent)
    BUCKET_CAP = 96             # a very common text fills its buckets; older entries fall out

    def __init__(self, hasher: Optional[MinHasher] = None, bands: int = 16, window_s: float = 180.0,
                 min_similarity: float = 0.6, min_length: int = 16, max_entries: int = 20000):
        self.hasher = hasher or MinHasher()
        self.bands = bands
        self.rows = self.hasher.n // bands
        self.window_s, self.min_similarity, self.min_length = window_s, min_similarity, min_length
        self._buckets: dict[tuple[int, bytes], deque] = {}
        self._order: deque = deque()
        self.max_entries = max_entries

    def _evict(self, now: float) -> None:
        while self._order and (now - self._order[0][0] > self.window_s or len(self._order) > self.max_entries):
            ts, keys, entry = self._order.popleft()
            for key in keys:
                bucket = self._buckets.get(key)
                while bucket and now - bucket[0].ts > self.window_s:
                    bucket.popleft()
                if bucket is not None and not bucket:
                    del self._buckets[key]

    def add(self, ts: float, author: int, channel_id: int, message_id: int, text: str) -> Optional[Cluster]:
        """Index the message; return the cluster of near-duplicates by *other* authors it joins, if any."""
        self._evict(ts)
        if len(normalise(text)) < self.min_length:
            return None
        sig = self.hasher.signature(text)
        if sig is None:
            return None
        entry = _Entry(ts, author, message_id, channel_id, sig)
        keys = [(b, sig[b * self.rows:(b + 1) * self.rows].tobytes()) for b in range(self.bands)]
        seen: dict[int, _Entry] = {}
        for key in keys:
            for other in self._buckets.get(key, ()):
                if other.message_id != message_id:
                    seen[other.message_id] = other
        cluster = Cluster()
        sims: list[float] = []
        if seen:
            others = sorted(seen.values(), key=lambda e: e.ts)[-self.MAX_CANDIDATES:]
            matrix = np.stack([o.signature for o in others])
            similarity = (matrix == sig).mean(axis=1)         # all candidates verified in one vector operation
            for other, sim in zip(others, similarity):
                if sim >= self.min_similarity:
                    cluster.authors.add(other.author)
                    cluster.message_ids.append((other.channel_id, other.message_id))
                    sims.append(float(sim))
        for key in keys:
            self._buckets.setdefault(key, deque(maxlen=self.BUCKET_CAP)).append(entry)
        self._order.append((ts, keys, entry))
        if not cluster.authors - {author}:
            return None
        cluster.authors.add(author)
        cluster.message_ids.append((channel_id, message_id))
        cluster.similarity = float(np.mean(sims))
        cluster.fingerprint = hashlib.blake2b(sig[:8].tobytes(), digest_size=6).hexdigest()
        return cluster

    def __len__(self) -> int:
        return len(self._order)


def snippet(text: str, limit: int = 200) -> str:
    """The most an incident ever keeps of a message: invisible characters removed, at most `limit` characters."""
    text = _SPACE.sub(" ", strip_invisible(text)).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def redact_mentions(text: str) -> str:
    return re.sub(r"<@[!&]?\d+>", "@user", text)


def unique(items: Iterable) -> list:
    return list(dict.fromkeys(items))

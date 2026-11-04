"""Feature hashing for the message-risk model: text -> a bag of bucket ids (the "hashing trick").

Tokens (each prefixed by its field so fields never collide by meaning):
    c:   character 3-, 4- and 5-grams of the normalised text (robust to misspellings and look-alike letters)
    w:   words, b: word bigrams
    h:/d:/t:/hc:  URL host labels, registrable domain, top-level domain, host character 4-grams
    lc:  the deterministic link-analysis codes (look-alike, punycode, shortener, ...): the rules become features
    m:   a few coarse shape facts (length, digit/upper-case share, pings, @everyone, URL count)
URLs are featurised from the *host only* (see docs/ML.md: in the public URL data, legitimate examples are mostly bare
home pages, so paths would teach the model "has a path = phishing", which is false on Discord).

Bucket = crc32(token) mod 2**18. Stable across processes and Python versions (unlike hash()).
"""
from __future__ import annotations

import re
import zlib
from typing import Iterable

import numpy as np

from rudhabot.security.links import analyse_url, extract_urls, registrable
from rudhabot.security.text import normalise

BUCKETS = 1 << 18
SCHEMA = f"sechash-v1-{BUCKETS}"
_WORD = re.compile(r"[a-z0-9$@#'%]+")
MAX_CHARS = 600


def _bucket(token: str) -> int:
    return zlib.crc32(token.encode("utf-8")) & (BUCKETS - 1)


def _shape(text: str) -> list[str]:
    n = len(text)
    digits = sum(ch.isdigit() for ch in text) / max(1, n)
    upper = sum(ch.isupper() for ch in text) / max(1, sum(ch.isalpha() for ch in text))
    out = [f"m:len{min(9, n.bit_length())}", f"m:dig{min(4, int(digits * 10))}", f"m:up{min(4, int(upper * 5))}"]
    pings = text.count("<@")
    if pings:
        out.append(f"m:pings{min(4, pings.bit_length())}")
    if "@everyone" in text or "@here" in text:
        out.append("m:everyone")
    return out


def host_tokens(url: str) -> list[str]:
    finding = analyse_url(url)
    if finding is None:
        return ["u:invalid"]
    host = finding.host.lower()
    labels = [x for x in host.split(".") if x]
    out = [f"d:{finding.domain}", f"t:{labels[-1] if labels else ''}", f"m:depth{min(5, len(labels))}",
           f"m:hostlen{min(8, len(host) // 8)}"]
    out += [f"h:{x}" for x in labels[:-1][-4:]]
    padded = f".{host}."
    out += [f"hc:{padded[i:i + 4]}" for i in range(max(0, len(padded) - 3))][:80]
    out += [f"lc:{code}" for code in finding.codes]
    if finding.official:
        out.append("lc:OFFICIAL")
    return out


def message_tokens(text: str) -> list[str]:
    raw = text[:MAX_CHARS * 2]
    norm = normalise(raw)[:MAX_CHARS]
    tokens = _shape(raw)
    words = _WORD.findall(norm)
    tokens += [f"w:{w}" for w in words[:150]]
    tokens += [f"b:{a}_{b}" for a, b in zip(words[:150], words[1:150])]
    padded = f" {norm} "
    for n in (3, 4, 5):
        tokens += [f"c:{padded[i:i + n]}" for i in range(max(0, len(padded) - n + 1))]
    urls = extract_urls(raw)
    tokens.append(f"m:urls{min(4, len(urls))}")
    for url in urls[:6]:
        tokens += host_tokens(url)
    return tokens


def url_only_tokens(url: str) -> list[str]:
    return ["m:url_only"] + host_tokens(url)


def encode(token_lists: Iterable[list[str]]) -> tuple[np.ndarray, np.ndarray]:
    """CSR-style: (indices int32, offsets int64 of length n+1). Duplicate tokens in one sample are kept once."""
    indices, offsets = [], [0]
    for tokens in token_lists:
        ids = sorted({_bucket(t) for t in tokens}) or [_bucket("m:empty")]
        indices.extend(ids)
        offsets.append(len(indices))
    return np.asarray(indices, dtype=np.int32), np.asarray(offsets, dtype=np.int64)


def encode_messages(texts: Iterable[str]) -> tuple[np.ndarray, np.ndarray]:
    return encode(message_tokens(t) for t in texts)


def encode_urls(urls: Iterable[str]) -> tuple[np.ndarray, np.ndarray]:
    return encode(url_only_tokens(u) for u in urls)


def registrable_of(url: str) -> str:
    finding = analyse_url(url)
    return finding.domain if finding else registrable(url)

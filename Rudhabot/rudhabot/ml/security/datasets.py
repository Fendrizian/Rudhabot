"""Training data for the message-risk model: public, licence-checked datasets plus synthetic server chat.

No Discord data from Rudhabot's own servers is used. The raw files are *not* committed (they contain other people's
messages); `fetch()` downloads them from their official sources and refuses any file whose SHA-256 differs from the
one recorded here, so a training run is reproducible and can't silently pick up a changed or tampered file.

    discord_wang   wangyuancheng/discord-phishing-scam-clean (Hugging Face, MIT): Discord messages labelled scam/clean
    discord_dqn    DQN-Labs/discord_scam_detection (Hugging Face, MIT): Discord scam/clean, balanced; overlaps the above,
                   so the two are de-duplicated by normalised text before splitting
    sms            UCI SMS Spam Collection (CC BY 4.0; Almeida & Hidalgo 2011): SMS spam/ham, for the spam head
    phiusiil       UCI PhiUSIIL Phishing URL Dataset (CC BY 4.0; Prasad & Chandra 2024): URLs labelled phishing (0)
                   or legitimate (1), for the URL head (host features only, see hashing.py)
    synthetic      micronation chat from the wargame simulator (normal messages only: chatter, battle commands,
                   legitimate links, greetings, celebrations) and Rudhabot's developer-written command phrasings,
                   as negatives in this server's own style

Splits are by a hash of the normalised text (messages) or of the registrable domain (URLs), so near-copies and pages
of one site never straddle train and test: 70% train, 10% validation (early stopping, calibration), 20% test.
"""
from __future__ import annotations

import csv
import hashlib
import os
import random
import urllib.request
import zipfile
from dataclasses import dataclass, field
from typing import Optional

from rudhabot.security.text import content_hash

SOURCES = {
    "discord_wang": {"url": "https://huggingface.co/datasets/wangyuancheng/discord-phishing-scam-clean/resolve/main/"
                            "discord-phishing-scam-detection.csv",
                     "file": "hf_wang/data.csv",
                     "sha256": "14c0b1207eecee8061076da6acd18da3a079bb88c4932b36deede2a21d3817d7", "licence": "MIT"},
    "discord_dqn": {"url": "https://huggingface.co/datasets/DQN-Labs/discord_scam_detection/resolve/main/data_balanced.csv",
                    "file": "hf_dqn/data.csv",
                    "sha256": "7893f2238786abc1049e6be0cfc3dff91f11d717bc2c958d9d9bbc352b8c6f03", "licence": "MIT"},
    "sms": {"url": "https://archive.ics.uci.edu/static/public/228/sms+spam+collection.zip", "file": "uci_sms/sms.zip",
            "member": "SMSSpamCollection", "extracted": "uci_sms/SMSSpamCollection",
            "sha256": "1587ea43e58e82b14ff1f5425c88e17f8496bfcdb67a583dbff9eefaf9963ce3",
            "extracted_sha256": "7d039a24a6083ed9ef0f806ebad56bbb976e3aeb8de05669173bfdc4996c239d",
            "licence": "CC BY 4.0"},
    "phiusiil": {"url": "https://archive.ics.uci.edu/static/public/967/phiusiil+phishing+url+dataset.zip",
                 "file": "uci_phiusiil/phi.zip", "member": "PhiUSIIL_Phishing_URL_Dataset.csv",
                 "extracted": "uci_phiusiil/PhiUSIIL_Phishing_URL_Dataset.csv",
                 "sha256": "0a639fd03aea6308c5b1c10c92aa23c2ce1505447a9137271865cd0badc9a59a",
                 "extracted_sha256": "a236549cd369cd80bd478ff8e1779cbf44c58d5c3f79f7a51a1adbed7d06d1c6",
                 "licence": "CC BY 4.0"},
}


class ChecksumError(RuntimeError):
    pass


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(raw_dir: str, log=print) -> None:
    """Download every source into raw_dir (skipping files already present) and verify checksums."""
    for name, src in SOURCES.items():
        path = os.path.join(raw_dir, src["file"])
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            log(f"downloading {name} ({src['licence']}) ...")
            request = urllib.request.Request(src["url"], headers={"User-Agent": "rudhabot-training"})
            with urllib.request.urlopen(request, timeout=120) as response, open(path + ".part", "wb") as out:
                while chunk := response.read(1 << 20):
                    out.write(chunk)
            os.replace(path + ".part", path)
        verify(raw_dir, name)
        if "member" in src:
            extracted = os.path.join(raw_dir, src["extracted"])
            if not os.path.exists(extracted):
                with zipfile.ZipFile(path) as archive:
                    with archive.open(src["member"]) as member, open(extracted, "wb") as out:
                        out.write(member.read())
            if sha256(extracted) != src["extracted_sha256"]:
                raise ChecksumError(f"{extracted}: unexpected contents")


def verify(raw_dir: str, name: str) -> None:
    src = SOURCES[name]
    path = os.path.join(raw_dir, src["file"])
    digest = sha256(path)
    if digest != src["sha256"]:
        raise ChecksumError(f"{name}: {path} has SHA-256 {digest}, expected {src['sha256']}")


@dataclass
class Example:
    text: str
    kind: str                         # message | url
    labels: dict                      # head -> 0/1 (missing = unlabelled for that head)
    source: str
    group: str = ""                   # split key
    split: str = ""
    tags: list = field(default_factory=list)


def split_of(group: str) -> str:
    bucket = int(hashlib.blake2b(group.encode("utf-8"), digest_size=4).hexdigest(), 16) % 100
    return "train" if bucket < 70 else "val" if bucket < 80 else "test"


def _read_csv(path: str) -> list[dict]:
    csv.field_size_limit(10 ** 7)
    with open(path, encoding="utf-8", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle))


def load_public(raw_dir: str, url_limit: Optional[int] = 80_000, seed: int = 0) -> list[Example]:
    out: list[Example] = []
    seen: set[str] = set()
    for name in ("discord_wang", "discord_dqn"):
        verify(raw_dir, name)
        for row in _read_csv(os.path.join(raw_dir, SOURCES[name]["file"])):
            text = (row.get("msg_content") or "").strip()
            label = (row.get("label") or "").strip()
            if not text or label not in ("0", "1"):
                continue
            key = content_hash(text)
            if key in seen:
                continue                                  # the two Discord sets overlap
            seen.add(key)
            y = int(label)
            out.append(Example(text[:2000], "message", {"scam": y, "spam": y}, name, key))
    sms = os.path.join(raw_dir, SOURCES["sms"]["extracted"])
    with open(sms, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "\t" not in line:
                continue
            tag, text = line.rstrip("\n").split("\t", 1)
            key = content_hash(text)
            if key in seen or tag not in ("ham", "spam"):
                continue
            seen.add(key)
            labels = {"spam": 1} if tag == "spam" else {"spam": 0, "scam": 0}
            out.append(Example(text[:2000], "message", labels, "sms", key))
    rows = _read_csv(os.path.join(raw_dir, SOURCES["phiusiil"]["extracted"]))
    rng = random.Random(seed)
    rng.shuffle(rows)
    by_label = {"0": [], "1": []}
    for row in rows:
        url, label = (row.get("URL") or "").strip(), (row.get("label") or "").strip()
        if url and label in by_label:
            by_label[label].append(url)
    half = (url_limit or 10 ** 9) // 2
    from rudhabot.ml.security.hashing import registrable_of
    for label, urls in by_label.items():
        for url in urls[:half]:
            out.append(Example(url[:500], "url", {"url": 1 if label == "0" else 0}, "phiusiil", registrable_of(url)))
    for ex in out:
        ex.split = split_of(ex.group)
    return out


def synthetic_chat(seed: int, n: int) -> list[Example]:
    """Normal messages in the simulated server's style (no attacks), plus developer-written command phrasings."""
    from rudhabot.ml.security.sim import Server
    server = Server(seed, members=400)
    out = []
    while len(out) < n:
        day = server.simulate_day((), normal_events=("announcement", "battle", "celebration", "onboarding_wave"))
        for e in day.events:
            content = getattr(e, "content", None)
            if content:
                out.append(Example(content, "message", {"scam": 0, "spam": 0}, "synthetic", content_hash(content)))
                if len(out) >= n:
                    break
    try:
        from rudhabot.ml.intent_data import TRAIN, TRAIN_V2_ADDITIONS
        phrasings = [text for table in (TRAIN, TRAIN_V2_ADDITIONS) for texts in table.values() for text in texts]
    except Exception:
        phrasings = []
    for text in phrasings:
        out.append(Example(text, "message", {"scam": 0, "spam": 0}, "developer", content_hash(text)))
    unique, seen = [], set()
    for ex in out:
        if ex.group not in seen:
            seen.add(ex.group)
            ex.split = split_of(ex.group)
            unique.append(ex)
    return unique


def summary(examples: list[Example]) -> dict:
    out: dict = {}
    for ex in examples:
        s = out.setdefault(ex.source, {"n": 0, "train": 0, "val": 0, "test": 0, "positive": {}})
        s["n"] += 1
        s[ex.split] += 1
        for head, y in ex.labels.items():
            s["positive"].setdefault(head, [0, 0])
            s["positive"][head][0] += y
            s["positive"][head][1] += 1
    return out

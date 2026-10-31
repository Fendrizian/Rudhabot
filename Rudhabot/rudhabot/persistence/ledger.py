"""Append-only JSON-lines ledger: one line per money movement (`ledger.jsonl`).

Append-only means history is never rewritten: a correction is a new entry. Each line is written and flushed in a single
`write` call, so a crash can at worst lose the last partial line, which `read()` skips. When the file passes
`rotate_bytes` it is renamed to `ledger-<timestamp>.jsonl` and a new file starts; old files are never deleted by the bot.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from typing import Iterator, Optional


class Ledger:
    def __init__(self, path: str, rotate_bytes: int = 50 * 1024 * 1024):
        self.path = path
        self.rotate_bytes = rotate_bytes

    def append(self, entry: dict) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if os.path.exists(self.path) and os.path.getsize(self.path) > self.rotate_bytes:
            os.replace(self.path, f"{os.path.splitext(self.path)[0]}-{int(time.time())}.jsonl")
        line = json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()

    def read(self) -> Iterator[dict]:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue                     # a torn last line after a crash

    def tail(self, n: int = 20, user_id: Optional[str] = None) -> list[dict]:
        keep: deque = deque(maxlen=n)
        for entry in self.read():
            if user_id is None or entry.get("user") == str(user_id):
                keep.append(entry)
        return list(keep)

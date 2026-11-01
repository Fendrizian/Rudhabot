"""Atomic JSON file storage.

All repositories are synchronous and never `await` between a load and the following save, so on asyncio's
single thread every read-modify-write is already atomic with respect to other coroutines. What *can* still go
wrong is the process dying mid-write, hence write-to-temp-then-`os.replace`.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Optional


class JsonStore:
    def __init__(self, path: str, default_factory: Callable[[], Any], *, indent: int | None = 4):
        self.path = path
        self._default_factory = default_factory
        self._indent = indent

    def load(self) -> Any:
        """Return the stored value, creating the file from the default if it does not exist.

        An empty file yields the default (untouched). A corrupt file is moved aside as `<name>.corrupt-<ts>`
        before the default is written, so a bad write can never silently destroy every account.
        """
        if not os.path.exists(self.path):
            value = self._default_factory()
            self.save(value)
            return value
        with open(self.path, "r", encoding="utf-8") as handle:
            content = handle.read().strip()
        if not content:
            return self._default_factory()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            os.replace(self.path, f"{self.path}.corrupt-{int(time.time())}")
            value = self._default_factory()
            self.save(value)
            return value

    def save(self, value: Any) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp_path = f"{self.path}.tmp"
        text = json.dumps(value, indent=self._indent)    # one C-encoded string: json.dump() streams through a far slower path
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self.path)


def json_copy(value: Any) -> Any:
    """A deep copy of JSON-shaped data (dicts, lists, scalars): several times faster than copy.deepcopy."""
    kind = type(value)
    if kind is dict:
        return {k: json_copy(v) for k, v in value.items()}
    if kind is list:
        return [json_copy(v) for v in value]
    return value


class OwnedJson:
    """The in-memory owner of one JSON file's data, for the hot stores (accounts, bank, TCG).

    `JsonStore.load()` re-reads and re-parses the whole file on every call: at 2,000 accounts that made one
    `UserRepository.update()` cost ~30 ms on the event loop. Here the parsed value is kept in memory, so reads cost
    nothing, while every `persist()` still writes through to disk atomically (same durability as before). If the file
    is changed on disk by someone else (e.g. an admin editing data.json while the bot runs), its modification time /
    size / inode no longer match and it is reloaded before the next read.

    Ownership rule: `value()` returns the live object. Mutate it only when you will `persist()`, and never hand it
    (or any part of it) to callers: give them `json_copy(...)` of what they need.
    """

    def __init__(self, store: JsonStore):
        self.store = store
        self._value: Any = None
        self._signature: Optional[tuple] = None

    def _disk_signature(self) -> Optional[tuple]:
        try:
            st = os.stat(self.store.path)
        except FileNotFoundError:
            return None
        return st.st_mtime_ns, st.st_size, st.st_ino

    def value(self) -> Any:
        if self._value is None or self._disk_signature() != self._signature:
            self._value = self.store.load()
            self._signature = self._disk_signature()
        return self._value

    def persist(self) -> None:
        self.store.save(self._value)
        self._signature = self._disk_signature()

    def replace(self, value: Any) -> None:
        self._value = value
        self.persist()

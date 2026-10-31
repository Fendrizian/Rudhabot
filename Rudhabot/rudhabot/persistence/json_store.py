"""Atomic JSON file storage.

All repositories are synchronous and never `await` between a load and the following save, so on asyncio's
single thread every read-modify-write is already atomic with respect to other coroutines. What *can* still go
wrong is the process dying mid-write, hence write-to-temp-then-`os.replace`.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable


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
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=self._indent)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self.path)

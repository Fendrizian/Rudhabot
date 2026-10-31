"""Who is currently inside a gambling game. Replaces the old global ACTIVE_GAMBLING_PLAYERS set."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rudhabot.errors import AlreadyInGame


class GamblingSessions:
    def __init__(self) -> None:
        self._active: set[int] = set()

    def __contains__(self, user_id: int) -> bool:
        return user_id in self._active

    def __len__(self) -> int:
        return len(self._active)

    def start(self, user_id: int) -> None:
        """Mark the user as playing. Used by games whose lifetime outlasts the command (button-driven games)."""
        self._active.add(user_id)

    def end(self, user_id: int) -> None:
        self._active.discard(user_id)

    @contextmanager
    def session(self, user_id: int) -> Iterator[None]:
        """Hold the lock for the duration of a self-contained game (e.g. slots). Always released."""
        if user_id in self._active:
            raise AlreadyInGame()
        self._active.add(user_id)
        try:
            yield
        finally:
            self._active.discard(user_id)

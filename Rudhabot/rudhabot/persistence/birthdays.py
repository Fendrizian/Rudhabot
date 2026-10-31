"""Birthday persistence: the registry of users' birthdays and the log of scheduled cake celebrations."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from rudhabot.domain.birthday import BirthdayDate
from rudhabot.persistence.json_store import JsonStore


class BirthdayRegistry:
    """birthday_registry.json: {user_id: {month, day, [year], [last_celebrated_year]}}"""

    def __init__(self, store: JsonStore):
        self._store = store

    def get(self, user_id: int | str) -> Optional[BirthdayDate]:
        record = self._store.load().get(str(user_id))
        return BirthdayDate.from_dict(record) if record else None

    def set(self, user_id: int | str, birthday: BirthdayDate) -> None:
        data = self._store.load()
        previous = data.get(str(user_id), {})
        record = birthday.to_dict()
        if "last_celebrated_year" in previous:       # changing the date must not re-trigger this year's gift
            record["last_celebrated_year"] = previous["last_celebrated_year"]
        data[str(user_id)] = record
        self._store.save(data)

    def remove(self, user_id: int | str) -> bool:
        data = self._store.load()
        if data.pop(str(user_id), None) is None:
            return False
        self._store.save(data)
        return True

    def all(self) -> dict[int, BirthdayDate]:
        return {int(uid): BirthdayDate.from_dict(rec) for uid, rec in self._store.load().items()}

    def last_celebrated_year(self, user_id: int | str) -> Optional[int]:
        return self._store.load().get(str(user_id), {}).get("last_celebrated_year")

    def mark_celebrated(self, user_id: int | str, year: int) -> None:
        data = self._store.load()
        if str(user_id) in data:
            data[str(user_id)]["last_celebrated_year"] = year
            self._store.save(data)


class CelebrationLog:
    """birthdays.json: the existing list of cake celebrations. Entries written by older versions have no
    `send_at`/`status` and are treated as already delivered."""

    def __init__(self, store: JsonStore):
        self._store = store

    def schedule(self, entry: dict, send_at: datetime) -> dict:
        entries = self._store.load()
        entry = {**entry, "id": uuid.uuid4().hex, "send_at": send_at.isoformat(), "status": "pending"}
        entries.append(entry)
        self._store.save(entries)
        return entry

    def due(self, now: datetime) -> list[dict]:
        return [e for e in self._store.load()
                if e.get("status") == "pending" and datetime.fromisoformat(e["send_at"]) <= now]

    def mark(self, entry_id: str, status: str, **extra) -> None:
        entries = self._store.load()
        for entry in entries:
            if entry.get("id") == entry_id:
                entry["status"] = status
                entry.update(extra)
        self._store.save(entries)

    def all(self) -> list[dict]:
        return self._store.load()

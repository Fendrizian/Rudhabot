"""Birthday persistence: the registry of users' birthdays and the log of scheduled cake celebrations."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from rudhabot.domain.birthday import BirthdayDate
from rudhabot.persistence.json_store import JsonStore


class BirthdayRegistry:
    """birthday_registry.json: {user_id: {month, day, [year], [last_celebrated_year]}}"""

    #: years already celebrated by members who removed their birthday: removing and re-adding it (to today) must not
    #: pay this year's gift again. Only the member id and that year are kept (never the date), and only until the
    #: year is over (`prune_celebrated`).
    CELEBRATED = "_celebrated"

    def __init__(self, store: JsonStore):
        self._store = store

    def get(self, user_id: int | str) -> Optional[BirthdayDate]:
        record = self._store.load().get(str(user_id))
        return BirthdayDate.from_dict(record) if record else None

    def set(self, user_id: int | str, birthday: BirthdayDate) -> None:
        data = self._store.load()
        uid = str(user_id)
        record = birthday.to_dict()
        celebrated = data.get(uid, {}).get("last_celebrated_year", data.get(self.CELEBRATED, {}).get(uid))
        if celebrated is not None:                   # changing the date must not re-trigger this year's gift
            record["last_celebrated_year"] = celebrated
        data.get(self.CELEBRATED, {}).pop(uid, None)
        data[uid] = record
        self._store.save(data)

    def remove(self, user_id: int | str, current_year: Optional[int] = None) -> bool:
        """Delete the birthday. If its gift was already paid in `current_year`, remember just that year."""
        data = self._store.load()
        record = data.pop(str(user_id), None)
        if record is None:
            return False
        year = record.get("last_celebrated_year")
        if year is not None and (current_year is None or year >= current_year):
            data.setdefault(self.CELEBRATED, {})[str(user_id)] = year
        self._store.save(data)
        return True

    def prune_celebrated(self, current_year: int) -> None:
        """Forget the remembered years of removed birthdays once those years are over (they can't block anything)."""
        data = self._store.load()
        kept = {uid: y for uid, y in data.get(self.CELEBRATED, {}).items() if y >= current_year}
        if kept != data.get(self.CELEBRATED, {}):
            if kept:
                data[self.CELEBRATED] = kept
            else:
                data.pop(self.CELEBRATED, None)
            self._store.save(data)

    def all(self) -> dict[int, BirthdayDate]:
        return {int(uid): BirthdayDate.from_dict(rec) for uid, rec in self._store.load().items()
                if uid != self.CELEBRATED}

    def last_celebrated_year(self, user_id: int | str) -> Optional[int]:
        data = self._store.load()
        uid = str(user_id)
        if uid in data:
            return data[uid].get("last_celebrated_year")
        return data.get(self.CELEBRATED, {}).get(uid)

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

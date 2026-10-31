"""Lottery bookkeeping (lottery.json): per-ticket totals (to audit the real return) and the jackpot history."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from rudhabot.domain.lottery import DrawBatch
from rudhabot.persistence.json_store import JsonStore

MAX_JACKPOT_HISTORY = 200


def _empty() -> dict:
    return {"stats": {}, "jackpots": []}


def empty_ledger() -> dict:
    return _empty()


class LotteryLedger:
    def __init__(self, store: JsonStore):
        self._store = store

    def record(self, user_id: int | str, batch: DrawBatch, at: datetime) -> None:
        data = self._store.load()
        stats = data.setdefault("stats", {}).setdefault(batch.spec.item, {"tickets": 0, "zonks": 0, "paid": 0, "spent": 0})
        stats["tickets"] += batch.tickets
        stats["zonks"] += batch.zonks
        stats["paid"] += batch.total_won
        stats["spent"] += batch.tickets * batch.spec.price
        for _ in range(batch.jackpots):
            data.setdefault("jackpots", []).append(
                {"user_id": str(user_id), "item": batch.spec.item, "amount": batch.spec.jackpot, "at": at.isoformat()}
            )
        data["jackpots"] = data.get("jackpots", [])[-MAX_JACKPOT_HISTORY:]
        self._store.save(data)

    def recent_jackpots(self, limit: int = 5, item: Optional[str] = None) -> list[dict]:
        wins = [w for w in self._store.load().get("jackpots", []) if item is None or w["item"] == item]
        return list(reversed(wins[-limit:]))

    def stats(self) -> dict:
        return self._store.load().get("stats", {})

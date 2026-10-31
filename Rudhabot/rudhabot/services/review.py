"""Review signals: Clairvoyance's anomaly monitor watches the audit trail and failed interactions and raises signals
for moderators. It never changes a balance, blocks a command or punishes anyone; a human decides (/review).

Flow   every audit entry and every failed interaction -> the user's current hourly window is re-judged (at most once
       a minute per user) -> a flag opens a review signal (at most one open signal per user per 6 hours), which is
       stored in reviews.json and posted to the review channel.
Model  refitted from the last 7 days of the ledger at startup and every 6 hours (`refit()`, run in a worker thread).
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Optional

from rudhabot.clock import Clock
from rudhabot.ml.anomaly import AnomalyDetector, Flag, windows
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.ledger import Ledger

log = logging.getLogger("rudhabot.review")
COOLDOWN = timedelta(hours=6)
REJUDGE_EVERY = timedelta(minutes=1)


class ReviewService:
    def __init__(self, store: JsonStore, ledger: Ledger, failures: Ledger, clock: Clock, enabled: bool = True):
        self._store, self._ledger, self._failures, self._clock = store, ledger, failures, clock
        self.enabled = enabled
        self.detector = AnomalyDetector()
        self._recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=2000))     # user -> recent audit entries
        self._fails: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        self._last_judged: dict[str, datetime] = {}
        self.pending: deque = deque(maxlen=200)                                       # signals waiting to be posted
        self.stats = {"judged": 0, "flags": 0}

    # ------------------------------------------------------------------ model
    def refit(self) -> dict:
        """Fit the detector on the last 7 days of hourly windows from the ledger (blocking: run in a thread)."""
        cutoff = (self._clock.now() - timedelta(days=7)).isoformat()
        entries = [e for e in self._ledger.read() if e.get("ts", "") >= cutoff]
        fails = [f for f in self._failures.read() if f.get("ts", "") >= cutoff]
        population = windows(entries, fails)
        fitted = self.detector.fit(population)
        log.info("anomaly model %s on %d hourly windows", "fitted" if fitted else "in cold start (rules only)", len(population))
        return {"windows": len(population), "fitted": fitted}

    # ------------------------------------------------------------------ inputs
    def on_audit(self, entry: dict) -> None:
        if not self.enabled:
            return
        self._recent[entry["user"]].append(entry)
        self._maybe_judge(entry["user"])

    def on_failure(self, user_id, what: str) -> None:
        if not self.enabled:
            return
        event = {"user": str(user_id), "ts": self._clock.now().isoformat(timespec="seconds"), "what": what[:80]}
        try:
            self._failures.append(event)
        except OSError:
            log.exception("could not log failed interaction")
        self._fails[event["user"]].append(event)
        self._maybe_judge(event["user"])

    # ------------------------------------------------------------------ judging
    def _maybe_judge(self, user: str) -> Optional[dict]:
        now = self._clock.now()
        last = self._last_judged.get(user)
        if last and now - last < REJUDGE_EVERY:
            return None
        self._last_judged[user] = now
        hour = now.isoformat()[:13]
        mine = [e for e in self._recent[user] if e["ts"][:13] == hour]
        fails = [f for f in self._fails[user] if f["ts"][:13] == hour]
        current = [w for w in windows(mine, fails) if w.user == user]
        self.stats["judged"] += 1
        if not current:
            return None
        flag = self.detector.judge(current[0])
        return self._open(flag) if flag else None

    def _open(self, flag: Flag) -> Optional[dict]:
        data = self._store.load()
        now = self._clock.now()
        for signal in data.get("signals", []):
            if (signal["user"] == flag.user and signal["status"] == "open"
                    and now - datetime.fromisoformat(signal["opened"]) < COOLDOWN):
                return None
        signal = {"id": uuid.uuid4().hex[:8], "user": flag.user, "hour": flag.hour, "opened": now.isoformat(timespec="seconds"),
                  "score": round(flag.score, 3), "kind": flag.kind, "reasons": list(flag.reasons), "status": "open",
                  "resolved_by": None, "note": None}
        data.setdefault("signals", []).append(signal)
        data["signals"] = data["signals"][-1000:]
        self._store.save(data)
        self.pending.append(signal)
        self.stats["flags"] += 1
        log.info("review signal %s opened for user %s (%s)", signal["id"], flag.user, flag.kind)
        return signal

    # ------------------------------------------------------------------ moderators
    def signals(self, status: Optional[str] = "open", limit: int = 10) -> list[dict]:
        items = [s for s in self._store.load().get("signals", []) if status is None or s["status"] == status]
        return items[-limit:][::-1]

    def resolve(self, signal_id: str, moderator_id, status: str, note: str = "") -> dict:
        if status not in ("dismissed", "confirmed"):
            raise ValueError("status must be dismissed or confirmed")
        data = self._store.load()
        for signal in data.get("signals", []):
            if signal["id"] == signal_id:
                signal.update(status=status, resolved_by=str(moderator_id), note=note[:300],
                              resolved=self._clock.now().isoformat(timespec="seconds"))
                self._store.save(data)
                return signal
        raise KeyError(signal_id)

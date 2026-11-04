"""Multi-scale activity features for the incident model: counts of what happened in the last 1, 5 and 15 minutes.

The same aggregator runs inside the bot and inside the wargame simulator, so the model is trained on exactly the
features it is served. Only counts and ratios are kept (per minute, for 15 minutes): no text, no ids.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from rudhabot.security.events import AuditEntry, MemberJoin, Message
from rudhabot.security.text import content_hash

COUNTERS = ("joins", "joins_young", "joins_no_avatar", "joins_flagged", "msgs", "msgs_new_member", "msgs_dup",
            "msgs_xdup", "mentions", "mention_heavy", "everyone", "links", "links_risky", "invites", "files_risky",
            "webhook_msgs", "audit_points", "bans", "kicks", "channel_deletes", "role_deletes", "escalations",
            "bot_adds", "webhook_creates", "config_weakened")
SCALES = (1, 5, 15)
EXTRA = ("authors_1", "authors_5", "author_concentration_1", "new_author_share_5", "join_ratio_5", "msg_ratio_5",
         "hour_sin", "hour_cos")
NAMES = tuple(f"{c}_{s}" for s in SCALES for c in COUNTERS) + EXTRA
DIM = len(NAMES)
SCHEMA = f"secfeat-v1-{DIM}"


class WindowFeatures:
    def __init__(self):
        self.minutes: deque = deque(maxlen=15)            # (minute index, counter dict, authors set, new authors set)
        self._minute = None
        self.join_rate = 0.25                             # EWMA joins per minute
        self.msg_rate = 5.0                               # EWMA messages per minute
        self._seen_hash: dict[str, tuple[float, int]] = {}

    @staticmethod
    def _new(minute: int):
        return (minute, dict.fromkeys(COUNTERS, 0.0), set(), set())

    def _fold(self, joins: float, msgs: float) -> None:
        self.join_rate = 0.98 * self.join_rate + 0.02 * min(joins, 60)
        self.msg_rate = 0.98 * self.msg_rate + 0.02 * min(msgs, 600)

    def _bucket(self, ts: float):
        minute = int(ts // 60)
        if self._minute is None:
            self._minute = minute
            self.minutes.append(self._new(minute))
        elif minute > self._minute:
            last = self.minutes[-1][1]
            self._fold(last["joins"], last["msgs"])
            for _ in range(min(minute - self._minute - 1, 15)):
                self._fold(0, 0)                          # quiet minutes count towards the usual rates too
            for m in range(max(self._minute + 1, minute - 15), minute):
                self.minutes.append(self._new(m))
            self.minutes.append(self._new(minute))
            self._minute = minute
            if len(self._seen_hash) > 5000:
                cutoff = ts - 900
                self._seen_hash = {h: v for h, v in self._seen_hash.items() if v[0] >= cutoff}
        return self.minutes[-1]                           # (late events land in the current minute)

    def add_join(self, e: MemberJoin) -> None:
        if e.bot:
            return
        _, c, _, _ = self._bucket(e.ts)
        c["joins"] += 1
        if e.ts - e.created_at < 7 * 86400:
            c["joins_young"] += 1
        if not e.has_avatar:
            c["joins_no_avatar"] += 1
        if e.flagged_spammer:
            c["joins_flagged"] += 1

    def add_message(self, e: Message, link_score: float = 0.0, links: int = 0, invites: int = 0,
                    risky_file: bool = False) -> None:
        if e.author_bot and e.webhook_id is None:
            return
        _, c, authors, new_authors = self._bucket(e.ts)
        if e.webhook_id is not None:
            c["webhook_msgs"] += 1
            return
        c["msgs"] += 1
        authors.add(e.author_id)
        if (e.member_age_days or 99) < 1:
            c["msgs_new_member"] += 1
            new_authors.add(e.author_id)
        if len(e.content) >= 8:
            h = content_hash(e.content)
            prev = self._seen_hash.get(h)
            if prev is not None and e.ts - prev[0] <= 300:
                c["msgs_dup" if prev[1] == e.author_id else "msgs_xdup"] += 1
            self._seen_hash[h] = (e.ts, e.author_id)
        c["mentions"] += e.user_mentions
        if e.user_mentions >= 5:
            c["mention_heavy"] += 1
        if e.everyone_attempt:
            c["everyone"] += 1
        c["links"] += links
        if link_score >= 0.5:
            c["links_risky"] += 1
        c["invites"] += invites
        if risky_file:
            c["files_risky"] += 1

    def add_audit(self, e: AuditEntry, points: float, escalation: bool, weakened: bool) -> None:
        _, c, _, _ = self._bucket(e.ts)
        c["audit_points"] += min(points, 50)
        key = {"ban": "bans", "kick": "kicks", "channel_delete": "channel_deletes", "role_delete": "role_deletes",
               "bot_add": "bot_adds", "webhook_create": "webhook_creates"}.get(e.action)
        if key:
            c[key] += 1
        if escalation:
            c["escalations"] += 1
        if weakened:
            c["config_weakened"] += 1

    def vector(self, now: float) -> np.ndarray:
        self._bucket(now)
        current = int(now // 60)
        out = []
        for scale in SCALES:
            agg = dict.fromkeys(COUNTERS, 0.0)
            for minute, c, _, _ in self.minutes:
                if current - minute < scale:
                    for k in COUNTERS:
                        agg[k] += c[k]
            out.extend(math.log1p(agg[k]) for k in COUNTERS)
        a1 = set().union(*(a for m, _, a, _ in self.minutes if current - m < 1)) if self.minutes else set()
        a5 = set().union(*(a for m, _, a, _ in self.minutes if current - m < 5)) if self.minutes else set()
        n5 = set().union(*(n for m, _, _, n in self.minutes if current - m < 5)) if self.minutes else set()
        msgs1 = sum(c["msgs"] for m, c, _, _ in self.minutes if current - m < 1)
        msgs5 = sum(c["msgs"] for m, c, _, _ in self.minutes if current - m < 5)
        joins5 = sum(c["joins"] for m, c, _, _ in self.minutes if current - m < 5)
        hour = (now % 86400) / 3600
        out.extend([
            math.log1p(len(a1)), math.log1p(len(a5)),
            (msgs1 / max(1, len(a1))) / 10.0,
            len(n5) / max(1, len(a5)),
            math.log1p(joins5 / max(0.05, 5 * self.join_rate)),
            math.log1p(msgs5 / max(1.0, 5 * self.msg_rate)),
            math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24),
        ])
        return np.asarray(out, dtype=np.float32)

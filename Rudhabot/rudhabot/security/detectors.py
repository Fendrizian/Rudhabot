"""Deterministic detectors for joins, messages and webhooks. Each turns events into Signals with reason codes.

Everything is in memory and bounded (deques with a maximum length, per-key maps pruned as they age). The only state
worth persisting is the join baseline (`JoinBaseline.to_dict()`), so a restart doesn't make every join look unusual.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

from rudhabot.security.events import MemberJoin, Message
from rudhabot.security.links import LinkReport, analyse_attachment
from rudhabot.security.policy import SecurityConfig
from rudhabot.security.reasons import duration
from rudhabot.security.signals import Reason, Signal
from rudhabot.security.text import CoordinationIndex, content_hash, name_shape, normalise, skeleton, snippet

DAY = 86400.0


def poisson_tail(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam), computed stably (used for 'is this burst unusual for this server?')."""
    if k <= 0:
        return 1.0
    lam = max(lam, 1e-9)
    term = math.exp(-lam)
    cdf = term
    for i in range(1, k):
        term *= lam / i
        cdf += term
        if term < 1e-300:
            break
    return max(0.0, 1.0 - cdf)


class Window:
    """Timestamps (with payloads) in a sliding window."""

    def __init__(self, maxlen: int = 512):
        self.items: deque = deque(maxlen=maxlen)

    def add(self, ts: float, item=None) -> None:
        self.items.append((ts, item))

    def prune(self, now: float, span: float) -> None:
        while self.items and now - self.items[0][0] > span:
            self.items.popleft()

    def within(self, now: float, span: float) -> list:
        return [(t, x) for t, x in self.items if now - t <= span]

    def count(self, now: float, span: float) -> int:
        return sum(1 for t, _ in self.items if now - t <= span)


# ============================================================================================ joins
class JoinBaseline:
    """Usual joins per minute for each hour of the week, as an exponentially weighted mean (alpha 0.05 per observed
    minute in that hour). Starts from a cautious prior so a new install doesn't alarm on the first busy hour."""
    PRIOR = 0.25          # joins per minute assumed before anything is learned (~360/day: generous for 1k members)

    def __init__(self, alpha: float = 0.05):
        self.alpha = alpha
        self.mean = [self.PRIOR] * 168
        self.seen = [0] * 168
        self._minute: Optional[int] = None
        self._count = 0

    @staticmethod
    def bucket(ts: float) -> int:
        return int(((ts // 3600) + 72) % 168)          # epoch day 0 was a Thursday; +72 h makes 0 = Monday 00:00

    def expected(self, ts: float) -> float:
        b = self.bucket(ts)
        learned = self.mean[b]
        # trust the learned rate only after ~2 hours of minutes in this bucket; never assume less than 0.02/min
        weight = min(1.0, self.seen[b] / 120)
        return max(0.02, weight * learned + (1 - weight) * self.PRIOR)

    def observe(self, ts: float) -> None:
        minute = int(ts // 60)
        if self._minute is None:
            self._minute = minute
        if minute != self._minute:
            self._close(self._minute, self._count)
            for gap in range(self._minute + 1, min(minute, self._minute + 180)):
                self._close(gap, 0)             # quiet minutes count too (capped, after a long outage)
            self._minute, self._count = minute, 0
        self._count += 1

    def _close(self, minute: int, count: int) -> None:
        b = self.bucket(minute * 60.0)
        self.mean[b] = (1 - self.alpha) * self.mean[b] + self.alpha * min(count, 120)
        self.seen[b] += 1

    def to_dict(self) -> dict:
        return {"mean": [round(x, 5) for x in self.mean], "seen": self.seen}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "JoinBaseline":
        out = cls()
        if d and len(d.get("mean", ())) == 168:
            out.mean = [float(x) for x in d["mean"]]
            out.seen = [int(x) for x in d.get("seen", [0] * 168)]
        return out


@dataclass
class _Joiner:
    user_id: int
    ts: float
    created_at: float
    has_avatar: bool
    shape: str
    stem: str
    avatar_key: Optional[str]
    invite: Optional[str]


def _traits(a: _Joiner, b: _Joiner) -> list[str]:
    out = []
    if abs(a.created_at - b.created_at) <= 2 * DAY and a.ts - a.created_at < 30 * DAY:
        out.append("accounts created within 2 days of each other")
    if not a.has_avatar and not b.has_avatar:
        out.append("no profile picture")
    if a.shape == b.shape and len(a.shape) >= 2:
        out.append("same name pattern")
    if a.stem and a.stem == b.stem:
        out.append("near-identical names")
    if a.avatar_key and a.avatar_key == b.avatar_key:
        out.append("identical profile picture")
    if a.invite and a.invite == b.invite:
        out.append("same invite")
    return out


class JoinDetector:
    def __init__(self, baseline: Optional[JoinBaseline] = None):
        self.baseline = baseline or JoinBaseline()
        self.recent: deque[_Joiner] = deque(maxlen=3000)
        self._last_surge = 0.0
        self._cohorts_reported: dict[str, int] = {}
        self.banned: dict[str, int] = {}      # fingerprint -> banned user id (avatar hash or name stem)

    def note_ban(self, user_id: int, name: str = "", avatar_key: Optional[str] = None) -> None:
        if avatar_key:
            self.banned[f"a:{avatar_key}"] = user_id
        stem = _stem(name)
        if len(stem) >= 5:
            self.banned[f"n:{stem}"] = user_id
        while len(self.banned) > 20000:
            self.banned.pop(next(iter(self.banned)))

    def observe(self, join: MemberJoin, cfg: SecurityConfig) -> list[Signal]:
        if join.bot:
            return []                          # bots are judged from the audit log (who added them)
        now = join.ts
        self.baseline.observe(now)
        me = _Joiner(join.user_id, now, join.created_at, join.has_avatar, name_shape(join.name), _stem(join.name),
                     join.avatar_key, join.invite_code)
        self.recent.append(me)
        window = float(cfg["joins.cohort_window_s"])
        while self.recent and now - self.recent[0].ts > max(window, 900):
            self.recent.popleft()
        signals = []
        surge = self._surge(now, cfg)
        if surge:
            signals.append(surge)
        cohort = self._cohort(me, now, window, cfg)
        if cohort:
            signals.append(cohort)
        if join.flagged_spammer:
            signals.append(Signal("spammer_flag", "raid", 0.45, 0.85, now, [Reason.of("JOIN_SPAMMER_FLAG")],
                                  users=[join.user_id]))
        evasion = self._evasion(join, me)
        if evasion:
            signals.append(evasion)
        return signals

    def _surge(self, now: float, cfg: SecurityConfig) -> Optional[Signal]:
        best = None
        for span in (60.0, 300.0):
            joins = [j for j in self.recent if now - j.ts <= span]
            count = len(joins)
            expected = self.baseline.expected(now) * span / 60
            need = cfg["joins.min_joins"] * (1 if span == 60 else 2)
            ratio = count / max(expected, 1e-6)
            if count < need or ratio < cfg["joins.surge_ratio"]:
                continue
            p = poisson_tail(count, expected)
            if p > 1e-3:
                continue
            severity = min(1.0, 0.35 + 0.12 * math.log2(max(1.0, ratio / cfg["joins.surge_ratio"])) + 0.01 * count)
            confidence = min(0.99, 0.6 + 0.04 * -math.log10(max(p, 1e-30)))
            sig = Signal("join_surge", "raid", severity, confidence, now,
                         [Reason.of("JOIN_SURGE", count=count, window=duration(span), ratio=round(ratio, 1))],
                         users=[j.user_id for j in joins][-200:],
                         evidence={"count": count, "window_s": span, "expected": round(expected, 3),
                                   "p_value": p})
            if best is None or sig.score > best.score:
                best = sig
        if best is None:
            return None
        if now - self._last_surge < 20 and best.evidence["count"] % 10:
            return None                                  # at most one report per 20 s, plus every 10 more joins
        self._last_surge = now
        return best

    def _cohort(self, me: _Joiner, now: float, window: float, cfg: SecurityConfig) -> Optional[Signal]:
        peers = [j for j in self.recent if j is not me and now - j.ts <= window]
        group, trait_counts = [me], defaultdict(int)
        for other in peers:
            shared = _traits(me, other)
            strong = "identical profile picture" in shared or "near-identical names" in shared
            if len(shared) >= 2 or strong:
                group.append(other)
                for t in shared:
                    trait_counts[t] += 1
        size = len(group)
        if size < cfg["joins.cohort_min"]:
            return None
        top = sorted(trait_counts, key=trait_counts.get, reverse=True)[:3]
        key = "|".join(top)
        reported = self._cohorts_reported.get(key, 0)
        if size < reported + 3 and reported:
            return None                                            # report growth in steps, not every join
        self._cohorts_reported[key] = size
        if len(self._cohorts_reported) > 200:
            self._cohorts_reported.pop(next(iter(self._cohorts_reported)))
        young = sum(1 for j in group if j.ts - j.created_at < cfg["joins.new_account_days"] * DAY)
        severity = min(1.0, 0.4 + 0.05 * size + 0.15 * young / size)
        confidence = min(0.97, 0.5 + 0.08 * len(top) + 0.02 * size)
        return Signal("join_cohort", "raid", severity, confidence, now,
                      [Reason.of("JOIN_COHORT", count=size, window=duration(window), traits=", ".join(top))],
                      users=[j.user_id for j in group][-200:],
                      evidence={"size": size, "traits": top, "young": young})

    def _evasion(self, join: MemberJoin, me: _Joiner) -> Optional[Signal]:
        hits = []
        if join.avatar_key and f"a:{join.avatar_key}" in self.banned:
            hits.append(("identical profile picture", self.banned[f"a:{join.avatar_key}"]))
        if len(me.stem) >= 5 and f"n:{me.stem}" in self.banned:
            hits.append(("near-identical name", self.banned[f"n:{me.stem}"]))
        if not hits:
            return None
        strong = any(h[0] == "identical profile picture" for h in hits)
        return Signal("ban_evasion", "evasion", 0.5, 0.75 if strong else 0.45, join.ts,
                      [Reason.of("JOIN_BAN_EVASION", traits=", ".join(h[0] for h in hits))],
                      users=[join.user_id], evidence={"matches": [h[1] for h in hits]})

    def state(self) -> dict:
        return {"baseline": self.baseline.to_dict()}


def _stem(name: str) -> str:
    """A name with digits, separators and look-alike letters removed ('xX_Gh0st_1234' -> 'xxghost')."""
    return "".join(ch for ch in skeleton(name) if ch.isalpha())[:24]


# ============================================================================================ messages
@dataclass
class MessageVerdict:
    signals: list[Signal]
    delete: bool = False           # deterministic evidence that this message is spam/malicious


class MessageDetector:
    def __init__(self):
        self.by_author: dict[int, Window] = defaultdict(lambda: Window(64))
        self.dups: dict[int, dict[str, Window]] = defaultdict(dict)
        self.mentions: dict[int, Window] = defaultdict(lambda: Window(64))
        self.channels: dict[int, Window] = defaultdict(lambda: Window(400))
        self.channel_rate: dict[int, float] = defaultdict(lambda: 2.0)       # usual messages per 10 s (EWMA)
        self.webhooks: dict[int, Window] = defaultdict(lambda: Window(128))
        self.coordination = CoordinationIndex()
        self.member_age: dict[int, float] = {}               # author -> days in the server at their last message
        self._last_prune = 0.0
        self._surge_reported: dict[int, float] = {}
        self._rate_updated: dict[int, float] = {}

    def prune(self, now: float) -> None:
        if now - self._last_prune < 60:
            return
        self._last_prune = now
        if len(self.member_age) > 20000:
            self.member_age.clear()
        for table, span in ((self.by_author, 120), (self.mentions, 120), (self.webhooks, 120)):
            for key in [k for k, w in table.items() if not w.items or now - w.items[-1][0] > span]:
                del table[key]
        for author in list(self.dups):
            per = self.dups[author]
            for h in [h for h, w in per.items() if not w.items or now - w.items[-1][0] > 3600]:
                del per[h]
            if not per:
                del self.dups[author]

    def observe(self, msg: Message, cfg: SecurityConfig, links: LinkReport) -> MessageVerdict:
        now = msg.ts
        self.prune(now)
        signals: list[Signal] = []
        delete = False
        ref = (msg.channel_id, msg.message_id)
        if msg.webhook_id is not None:
            return self._webhook(msg, now)
        relax = cfg["messages.relaxed_factor"] if cfg.relaxed(msg.channel_id) else 1.0
        staff = msg.author_tier >= 1
        surge = self._channel(msg, now, relax)
        if surge:
            signals.append(surge)
        snip = snippet(msg.content)

        if not staff:
            # ---------------------------------------------------------------- floods
            win = self.by_author[msg.author_id]
            win.add(now, ref)
            span = cfg["messages.flood_window_s"]
            recent = win.within(now, span)
            limit = int(math.ceil(cfg["messages.flood_count"] * relax))
            if len(recent) >= limit:
                severity = min(1.0, 0.45 + 0.05 * (len(recent) - limit))
                signals.append(Signal("flood", "spam", severity, 0.9, now,
                                      [Reason.of("MSG_FLOOD", count=len(recent), window=duration(span))],
                                      users=[msg.author_id], channels=[msg.channel_id],
                                      messages=[r for _, r in recent], evidence={"count": len(recent)}))
                delete = True
            # ---------------------------------------------------------------- duplicates
            if msg.content.strip() or msg.attachments:
                h = content_hash(msg.content + "|" + ",".join(a.filename for a in msg.attachments))
                per = self.dups[msg.author_id]
                dwin = per.setdefault(h, Window(64))
                dwin.add(now, ref)
                dspan = cfg["messages.dup_window_s"]
                repeats = dwin.within(now, dspan)
                dlimit = int(math.ceil(cfg["messages.dup_count"] * relax))
                if len(repeats) >= dlimit and len(msg.content.strip()) >= 2:
                    signals.append(Signal("duplicate", "spam", min(1.0, 0.5 + 0.05 * (len(repeats) - dlimit)), 0.92,
                                          now, [Reason.of("MSG_DUPLICATE", count=len(repeats), window=duration(dspan))],
                                          users=[msg.author_id], channels=[msg.channel_id],
                                          messages=[r for _, r in repeats], evidence={"hash": h},
                                          snippet=snip))
                    delete = True
            # ---------------------------------------------------------------- mentions
            if msg.user_mentions >= cfg["messages.mention_limit"]:
                signals.append(Signal("mention_bomb", "spam", min(1.0, 0.6 + 0.02 * msg.user_mentions), 0.95, now,
                                      [Reason.of("MSG_MENTION_BOMB", count=msg.user_mentions)],
                                      users=[msg.author_id], channels=[msg.channel_id], messages=[ref],
                                      evidence={"mentions": msg.user_mentions}))
                delete = True
            if msg.user_mentions:
                mwin = self.mentions[msg.author_id]
                mwin.add(now, msg.user_mentions)
                total = sum(n for _, n in mwin.within(now, 60))
                if total >= cfg["messages.mention_window_limit"] * relax and msg.user_mentions < cfg["messages.mention_limit"]:
                    signals.append(Signal("mention_flood", "spam", min(1.0, 0.5 + 0.01 * total), 0.85, now,
                                          [Reason.of("MSG_MENTION_FLOOD", count=total, window="1 min")],
                                          users=[msg.author_id], channels=[msg.channel_id], messages=[ref]))
                    delete = True
            if msg.everyone_attempt:
                signals.append(Signal("everyone_attempt", "spam", 0.3, 0.7, now, [Reason.of("MSG_EVERYONE_ATTEMPT")],
                                      users=[msg.author_id], channels=[msg.channel_id], messages=[ref]))
            # ---------------------------------------------------------------- invites
            if links.invites and cfg["messages.invites"] != "allow":
                young = (msg.member_age_days or 99) < 1
                signals.append(Signal("invite", "spam", 0.5 if young else 0.35, 0.9, now,
                                      [Reason.of("MSG_INVITE")], users=[msg.author_id], channels=[msg.channel_id],
                                      messages=[ref], evidence={"invites": links.invites[:5]}, snippet=snip))
                if cfg["messages.invites"] == "delete":
                    delete = True
            # ---------------------------------------------------------------- coordination (several accounts)
            self.member_age[msg.author_id] = msg.member_age_days if msg.member_age_days is not None else 99.0
            cluster = self.coordination.add(now, msg.author_id, msg.channel_id, msg.message_id, msg.content)
            if cluster is not None and len(cluster.authors) >= cfg["messages.coord_accounts"]:
                family = "phishing" if links.links else "spam"
                # Many established members writing the same thing is usually a celebration ("happy birthday!"),
                # not spam: only a payload (links, invites, pings) or mostly brand-new accounts makes it actionable.
                # In relaxed channels (games, roleplay battles) repeated commands with a few pings are the point,
                # so only links or invites count there; elsewhere mass pings count too.
                payload = bool(links.links or links.invites) or (relax == 1.0 and (msg.everyone_attempt
                                                                                    or msg.user_mentions >= 4))
                new_share = sum(1 for a in cluster.authors if self.member_age.get(a, 99.0) < 1) / len(cluster.authors)
                # brand-new members greeting in the same words ("came from the partner post!") is an onboarding
                # wave, so without a payload only longer text from mostly new accounts is treated as spam
                actionable = payload or (new_share >= 0.5 and len(normalise(msg.content)) >= 40)
                signals.append(Signal("coordinated", family,
                                      min(1.0, 0.55 + 0.05 * len(cluster.authors)) if actionable else 0.25,
                                      0.9 if actionable else 0.6, now,
                                      [Reason.of("MSG_COORDINATED", accounts=len(cluster.authors),
                                                 window=duration(self.coordination.window_s),
                                                 similarity=round(cluster.similarity, 2))],
                                      users=sorted(cluster.authors), channels=[msg.channel_id],
                                      messages=cluster.message_ids[-100:],
                                      evidence={"fingerprint": cluster.fingerprint, "accounts": len(cluster.authors),
                                                "new_share": round(new_share, 2), "actionable": actionable},
                                      snippet=snip if actionable else None))
                delete = delete or actionable

        # -------------------------------------------------------------------- links and files: everyone but bots
        if links.links or links.masked_mismatch:
            score = links.score
            if cfg["links.shorteners"] == "allow":
                score = max((f.score for f in links.links if "LINK_SHORTENER" not in f.codes), default=0.0) \
                    if not links.masked_mismatch else score
            if score >= 0.3:
                reasons = []
                for f in links.links:
                    for code in f.codes:
                        reasons.append(Reason.of(code, domain=f.domain, brand=(f.brand or "a known site").title()))
                if links.masked_mismatch:
                    reasons.append(Reason.of("LINK_MASKED_MISMATCH"))
                if links.links and not all(f.official for f in links.links):
                    reasons.extend(Reason.of(f"LURE_{k.upper()}") for k in links.lures)
                age = msg.member_age_days
                if age is not None and age < 1 and not staff:
                    reasons.append(Reason.of("MSG_NEW_ACCOUNT_LINK", age=duration(age * DAY)))
                    score = min(1.0, score + 0.1)
                signals.append(Signal("link", "phishing", score, 0.85, now, _dedupe(reasons), users=[msg.author_id],
                                      channels=[msg.channel_id], messages=[ref],
                                      evidence={"domains": sorted({f.domain for f in links.links})[:8],
                                                "score": round(score, 3)}, snippet=snip))
                if score >= cfg["links.delete_score"] or (cfg["links.shorteners"] == "delete" and
                                                          any("LINK_SHORTENER" in f.codes for f in links.links)):
                    delete = True
        for att in msg.attachments:
            finding = analyse_attachment(att.filename, att.size, att.content_type)
            if finding and finding.score >= 0.5:
                signals.append(Signal("attachment", "phishing", finding.score, 0.85, now,
                                      [Reason.of(c, filename=finding.filename) for c in finding.codes],
                                      users=[msg.author_id], channels=[msg.channel_id], messages=[ref],
                                      evidence={"filename": finding.filename}))
                if finding.score >= cfg["files.delete_score"]:
                    delete = True
        return MessageVerdict(signals, delete)

    def _channel(self, msg: Message, now: float, relax: float) -> Optional[Signal]:
        win = self.channels[msg.channel_id]
        win.add(now, msg.author_id)
        win.prune(now, 60)
        count = win.count(now, 10)
        usual = self.channel_rate[msg.channel_id]
        if now - self._rate_updated.get(msg.channel_id, 0.0) >= 10:      # learn the usual rate every 10 s
            self._rate_updated[msg.channel_id] = now
            self.channel_rate[msg.channel_id] = 0.95 * usual + 0.05 * max(0.5, float(count))
        threshold = max(15.0 * relax, 5.0 * usual * relax)
        if count < threshold or now - self._surge_reported.get(msg.channel_id, 0) < 30:
            return None
        self._surge_reported[msg.channel_id] = now
        authors = len({a for _, a in win.within(now, 10)})
        return Signal("channel_surge", "spam", min(1.0, 0.35 + 0.01 * count), 0.8, now,
                      [Reason.of("MSG_CHANNEL_SURGE", count=count, window="10s", ratio=round(count / max(usual, 1), 1))],
                      channels=[msg.channel_id], evidence={"count": count, "usual": round(usual, 2),
                                                           "authors": authors})

    def _webhook(self, msg: Message, now: float) -> MessageVerdict:
        win = self.webhooks[msg.webhook_id]
        win.add(now, (msg.channel_id, msg.message_id, msg.user_mentions + (25 if msg.everyone_attempt else 0)))
        recent = win.within(now, 10)
        pings = sum(x[2] for _, x in recent)
        minute = win.within(now, 60)
        everyone_repeats = sum(1 for _, x in minute if x[2] >= 25)          # a webhook pinging @everyone again and again
        if len(recent) >= 10 or (len(recent) >= 4 and pings >= 10) or everyone_repeats >= 3:
            recent = minute if everyone_repeats >= 3 else recent
            sig = Signal("webhook_flood", "integration", min(1.0, 0.6 + 0.02 * len(recent) + 0.005 * pings), 0.9, now,
                         [Reason.of("WEBHOOK_FLOOD", count=len(recent), window="10s")],
                         users=[msg.author_id], channels=[msg.channel_id], messages=[(c, m) for _, (c, m, _) in recent],
                         evidence={"webhook_id": msg.webhook_id, "count": len(recent), "pings": pings})
            return MessageVerdict([sig], True)
        return MessageVerdict([])


def _dedupe(reasons: list[Reason]) -> list[Reason]:
    seen, out = set(), []
    for r in reasons:
        if r.code not in seen:
            seen.add(r.code)
            out.append(r)
    return out

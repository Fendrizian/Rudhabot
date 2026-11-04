"""The detection pipeline for one server: event -> signals -> incidents -> threat level -> planned actions.

Synchronous, deterministic and free of Discord: the service feeds it normalised events and carries out what it plans;
the simulator feeds it synthetic events and scores what it plans. Model scores arrive *after* the deterministic pass
(`observe_model`), so a slow or missing model never delays the rules.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from rudhabot.security.antinuke import AntiNuke, StaffBaseline
from rudhabot.security.detectors import DAY, JoinBaseline, JoinDetector, MessageDetector
from rudhabot.security.events import AuditEntry, MemberJoin, MemberLeave, Message, Tick
from rudhabot.security.features import WindowFeatures
from rudhabot.security.levels import Level, ThreatState, suggested_level
from rudhabot.security.links import LinkReport, analyse_attachment, analyse_text
from rudhabot.security.policy import SecurityConfig
from rudhabot.security.responses import PlannedAction, Planner
from rudhabot.security.signals import Reason, Signal

RULES_VERSION = "rules-2026.11-1"
INCIDENT_GAP_S = 900              # a signal joins an open incident of its family if it was active this recently
INCIDENT_CLOSE_S = 1800           # an incident with no new signals for this long is closed
MIN_INCIDENT_SCORE = 0.2          # weaker single signals are not worth an incident
MODEL_BONUS_CAP = 0.15            # the most a model can add to an incident's severity
MODEL_REVIEW_CAP = 0.6            # ... when its reading is corroborated (a confident scam score on a message with an
                                  # unofficial link): enough to alert staff for review, never to move the threat level


@dataclass
class Incident:
    id: str
    guild_id: int
    family: str
    opened: float
    last: float
    status: str = "open"
    severity: float = 0.0             # rules + capped model contribution
    rule_severity: float = 0.0        # deterministic signals only (this is what moves the threat level)
    model_bonus: float = 0.0
    peak_level: int = 1
    counts: dict = field(default_factory=dict)
    top: dict = field(default_factory=dict)          # detector -> best score
    users: list = field(default_factory=list)
    channels: list = field(default_factory=list)
    timeline: list = field(default_factory=list)
    signals: list = field(default_factory=list)      # the most recent 60 signals (structured, no text)
    actions: list = field(default_factory=list)      # {id, kind, authority, status}
    snippets: list = field(default_factory=list)     # (channel, message, snippet) of flagged messages (<= 200 chars)
    versions: dict = field(default_factory=dict)
    closed_at: Optional[float] = None
    notes: list = field(default_factory=list)
    alerted: bool = False

    def note(self, ts: float, kind: str, text: str) -> None:
        self.timeline.append({"ts": ts, "kind": kind, "text": text[:300]})
        if len(self.timeline) > 300:
            del self.timeline[1:51]                   # keep the opening entry and the most recent history

    def add(self, signal: Signal) -> None:
        self.last = max(self.last, signal.ts)
        self.counts[signal.detector] = self.counts.get(signal.detector, 0) + 1
        if signal.source == "model":
            if signal.evidence.get("corroborated"):        # 0.9 -> 0.54, 0.97 -> 0.58: an alert for review
                bonus = signal.confidence * MODEL_REVIEW_CAP
            else:
                bonus = signal.score * MODEL_BONUS_CAP
            self.model_bonus = min(MODEL_REVIEW_CAP, max(self.model_bonus, bonus))
        else:
            self.top[signal.detector] = max(self.top.get(signal.detector, 0.0), signal.score)
            scores = sorted(self.top.values(), reverse=True)[:5]
            self.rule_severity = 1 - math.prod(1 - s for s in scores)
        self.severity = min(1.0, self.rule_severity + self.model_bonus * (1 - self.rule_severity))
        for u in signal.users:
            if u and u not in self.users and len(self.users) < 500:
                self.users.append(u)
        for c in signal.channels:
            if c and c not in self.channels and len(self.channels) < 100:
                self.channels.append(c)
        self.signals.append(signal.to_dict())
        if len(self.signals) > 60:
            del self.signals[:-60]
        if signal.snippet and signal.messages and len(self.snippets) < 25:
            ch, mid = signal.messages[-1]
            self.snippets.append({"channel": ch, "message": mid, "author": signal.users[0] if signal.users else None,
                                  "text": signal.snippet[:200], "ts": signal.ts})
        self.note(signal.ts, "signal", signal.summary())

    def headline(self) -> str:
        return {"raid": "Possible raid", "spam": "Spam", "phishing": "Phishing / malicious links",
                "nuke": "High-impact changes (anti-nuke)", "integration": "Webhook / bot activity",
                "evasion": "Possible ban evasion", "model": "Unusual activity pattern"}.get(self.family, self.family)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict) -> "Incident":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Outcome:
    signals: list[Signal] = field(default_factory=list)
    plans: list[PlannedAction] = field(default_factory=list)
    level_change: Optional[tuple[int, int, str]] = None
    opened: list[Incident] = field(default_factory=list)
    updated: list[Incident] = field(default_factory=list)
    closed: list[Incident] = field(default_factory=list)
    model_candidate: bool = False          # the message is worth scoring with the message model

    def merge(self, other: "Outcome") -> "Outcome":
        self.signals += other.signals
        self.plans += other.plans
        self.level_change = other.level_change or self.level_change
        for name in ("opened", "updated", "closed"):
            mine = getattr(self, name)
            for inc in getattr(other, name):
                if all(inc is not x for x in mine):
                    mine.append(inc)
        self.model_candidate = self.model_candidate or other.model_candidate
        return self


Exemption = Callable[[str, int, str], bool]          # (kind: user|role|channel, id, detector) -> exempt?
#: detectors answered by their own targeted actions (removing a slow-drip wave, probation for its accounts): they
#: alert, but never raise the whole server's threat level, which would restrict every new member for hours
LEVEL_NEUTRAL = frozenset({"slow_drip"})


class GuildEngine:
    def __init__(self, guild_id: int, config: SecurityConfig, *, bot_id: int = 0, state: Optional[dict] = None,
                 exempt: Optional[Exemption] = None, model_versions: Optional[dict] = None):
        state = state or {}
        self.guild_id, self.cfg, self.bot_id = guild_id, config, bot_id
        self.threat = ThreatState.from_dict(state.get("threat"))
        self.joins = JoinDetector(JoinBaseline.from_dict(state.get("baseline")))
        self.messages = MessageDetector()
        self.antinuke = AntiNuke(bot_id)
        self.antinuke.baseline = StaffBaseline.from_dict(state.get("staff"))
        self.planner = Planner()
        self.features = WindowFeatures()
        self.incidents: dict[str, Incident] = {}
        self.exempt: Exemption = exempt or (lambda kind, ident, detector: False)
        self.own_invites: set[str] = set()
        self.extra_allow: set[str] = set()
        self.model_versions = model_versions or {}
        self._ids = itertools.count(1)
        self.counters = {"events": 0, "signals": 0, "plans": 0}
        self.message_bar = 0.8              # confidence a model reading needs to count; raised by staff verdicts
        self.incident_bar = 0.8

    # ------------------------------------------------------------------ persistence
    def state(self) -> dict:
        return {"threat": self.threat.to_dict(), "baseline": self.joins.baseline.to_dict(),
                "staff": self.antinuke.baseline.to_dict()}

    @property
    def level(self) -> Level:
        return self.threat.level

    # ------------------------------------------------------------------ entry points
    def observe(self, event) -> Outcome:
        self.counters["events"] += 1
        if isinstance(event, Message):
            out = self._message(event)
        elif isinstance(event, MemberJoin):
            out = self._join(event)
        elif isinstance(event, AuditEntry):
            out = self._audit(event)
        elif isinstance(event, Tick):
            out = self.tick(event.ts)
        elif isinstance(event, MemberLeave):
            out = Outcome()
        else:
            raise TypeError(f"unknown event {type(event).__name__}")
        self.counters["signals"] += len(out.signals)
        self.counters["plans"] += len(out.plans)
        return out

    def _message(self, msg: Message) -> Outcome:
        if msg.author_id == self.bot_id or (msg.author_bot and msg.webhook_id is None):
            return Outcome()
        if self.cfg.ignored(msg.channel_id) or self.exempt("channel", msg.channel_id, "messages") \
                or self.exempt("user", msg.author_id, "messages") \
                or any(self.exempt("role", r, "messages") for r in msg.author_roles):
            return Outcome()
        links = analyse_text(msg.content, self.cfg["links.deny"], set(self.cfg["links.allow"]) | self.extra_allow,
                             self.own_invites) if msg.content else LinkReport()
        verdict = self.messages.observe(msg, self.cfg, links)
        risky_file = any((f := analyse_attachment(a.filename, a.size, a.content_type)) and f.score >= 0.5
                         for a in msg.attachments)
        self.features.add_message(msg, links.score, len(links.links), len(links.invites), risky_file)
        out = self._absorb(verdict.signals, msg.ts)
        out.plans += self._tag(self.planner.for_message(verdict.signals, verdict.delete, self.cfg, self.threat.orders,
                                                        msg.ts, msg.author_id, msg.channel_id, msg.member_age_days,
                                                        author_tier=msg.author_tier,
                                                        webhook=msg.webhook_id is not None), verdict.signals)
        # worth a model look: links, files, invites, new members, or anything a rule already flagged
        out.model_candidate = bool(links.links or links.invites or msg.attachments or verdict.signals
                                   or (msg.member_age_days is not None and msg.member_age_days < 14)
                                   or (msg.account_age_days is not None and msg.account_age_days < 30))
        return out

    def observe_model(self, msg: Message, scam: float, spam: float, uncertain: bool = False,
                      version: str = "") -> Outcome:
        """The message model's view of a message already handled by the rules. Advisory: it adds a model signal
        (which can raise an incident's severity by at most MODEL_BONUS_CAP), and can corroborate deleting a message
        that a rule already found a risky link in."""
        if uncertain or max(scam, spam) < self.message_bar:
            return Outcome()
        detector, code, score = ("model_scam", "MSG_MODEL_SCAM", scam) if scam >= spam else \
            ("model_spam", "MSG_MODEL_SPAM", spam)
        links = analyse_text(msg.content, self.cfg["links.deny"], set(self.cfg["links.allow"]) | self.extra_allow,
                             self.own_invites)
        corroborated = scam >= max(0.9, self.message_bar) and bool(links.links) and not all(f.official for f in links.links)
        sig = Signal(detector, "phishing" if detector == "model_scam" else "spam", 0.6, score, msg.ts,
                     [Reason.of(code, confidence=round(score, 2))], users=[msg.author_id], channels=[msg.channel_id],
                     messages=[(msg.channel_id, msg.message_id)], source="model",
                     evidence={"scam": round(scam, 3), "spam": round(spam, 3), "version": version,
                               "corroborated": corroborated}, snippet=msg.content[:200] if corroborated else None)
        out = self._absorb([sig], msg.ts, allow_new=True)
        if links.links and links.score >= 0.5 and not links.score >= self.cfg["links.delete_score"]:
            link_sig = Signal("link", "phishing", links.score, 0.85, msg.ts, [], messages=[(msg.channel_id, msg.message_id)])
            out.plans += self._tag(self.planner.for_message([link_sig, sig], False, self.cfg, self.threat.orders, msg.ts,
                                                            msg.author_id, msg.channel_id, msg.member_age_days,
                                                            model_scam=scam, author_tier=msg.author_tier), [sig])
        return out

    def _join(self, join: MemberJoin) -> Outcome:
        if self.exempt("user", join.user_id, "joins"):
            return Outcome()
        signals = self.joins.observe(join, self.cfg)
        self.features.add_join(join)
        out = self._absorb(signals, join.ts)
        age = (join.ts - join.created_at) / DAY
        if not join.bot:
            out.plans += self._tag(self.planner.for_join(signals, self.cfg, self.threat.orders, join.ts, join.user_id,
                                                         age), signals)
        return out

    def _audit(self, entry: AuditEntry) -> Outcome:
        exempt = self.exempt("user", entry.actor_id, "antinuke") or any(
            self.exempt("role", r, "antinuke") for r in entry.actor_roles)
        if entry.action == "ban" and entry.target_id:
            self.joins.note_ban(entry.target_id)
        signals = self.antinuke.observe(entry, self.cfg, self.threat.orders.budget_factor, exempt=exempt)
        points = self.antinuke.weight(entry)[0] if entry.actor_id != self.bot_id else 0.0
        self.features.add_audit(entry, points, any(s.detector == "escalation" for s in signals),
                                any(s.detector == "config_weakened" for s in signals))
        out = self._absorb(signals, entry.ts)
        if any(s.detector == "nuke_budget" for s in signals):
            span = float(self.cfg["antinuke.window_s"])
            contained = {
                "webhook_create": self.antinuke.added_by(entry.actor_id, "webhook_create", entry.ts, span),
                "bot_add": self.antinuke.added_by(entry.actor_id, "bot_add", entry.ts, span),
                "escalations": self._recent_escalations(entry.actor_id, entry.ts, span),
            }
            out.plans += self._tag(self.planner.for_audit(signals, self.cfg, entry.ts, entry.actor_tier, contained),
                                   signals)
        elif any(s.detector == "escalation" for s in signals):
            out.plans += self._tag(self.planner.for_audit(signals, self.cfg, entry.ts, entry.actor_tier, {}), signals)
        return out

    def _recent_escalations(self, actor: int, now: float, span: float) -> list[dict]:
        found = []
        for inc in self.incidents.values():
            for s in inc.signals:
                if s["detector"] == "escalation" and s.get("actor") == actor and now - s["ts"] <= span \
                        and s["evidence"].get("everyone") is not None and s["evidence"].get("role"):
                    found.append({"role": s["evidence"]["role"], "gained": s["evidence"].get("gained", 0)})
        return found

    # ------------------------------------------------------------------ incidents and the threat level
    def _new_id(self, ts: float) -> str:
        return f"{int(ts) % 10**7:07d}{next(self._ids):02d}"

    def _absorb(self, signals: list[Signal], now: float, allow_new: bool = True) -> Outcome:
        out = Outcome(signals=list(signals))
        for sig in signals:
            if sig.score < MIN_INCIDENT_SCORE:
                continue                    # observed (and counted in features) but too weak to be evidence
            inc = self._incident_for(sig)
            if inc is None:
                if not allow_new:
                    continue
                inc = Incident(self._new_id(sig.ts), self.guild_id, sig.family if sig.source != "model" or
                               sig.family in ("phishing", "spam") else "model", sig.ts, sig.ts,
                               peak_level=int(self.threat.level),
                               versions={"rules": RULES_VERSION, "config": self.cfg.version, **self.model_versions})
                inc.note(sig.ts, "opened", f"{inc.headline()} detected")
                self.incidents[inc.id] = inc
                out.opened.append(inc)
            inc.add(sig)
            if inc not in out.opened and all(inc is not x for x in out.updated):
                out.updated.append(inc)
        for inc in out.opened + out.updated:
            change = self._escalate(inc, now)
            if change:
                out.level_change = change
                inc.note(now, "level", f"threat level raised to {Level(change[1]).label}: {change[2]}")
                out.plans += self._tag(self.planner.for_level(self.threat.level, self.threat.orders, self.cfg, now,
                                                              inc.id, inc.channels), [], inc)
            inc.peak_level = max(inc.peak_level, int(self.threat.level))
        return out

    def _incident_for(self, sig: Signal) -> Optional[Incident]:
        candidates = [i for i in self.incidents.values() if i.status == "open" and sig.ts - i.last <= INCIDENT_GAP_S]
        same = [i for i in candidates if i.family == sig.family]
        if sig.family == "nuke" and sig.actor is not None:
            # one incident per account acting: two staff accounts misbehaving are two investigations
            same = [i for i in same if not i.users or sig.actor in i.users] or []
        if same:
            return max(same, key=lambda i: i.last)
        if sig.family in ("spam", "phishing") and sig.users:
            raid = [i for i in candidates if i.family == "raid" and set(sig.users) & set(i.users)]
            if raid:
                return max(raid, key=lambda i: i.last)      # spam from accounts that just joined in a raid
        return None

    def _escalate(self, inc: Incident, now: float) -> Optional[tuple[int, int, str]]:
        active = [i for i in self.incidents.values() if i.status == "open" and now - i.last <= INCIDENT_GAP_S
                  and i.rule_severity >= 0.5]
        families = len({i.family for i in active})
        target = suggested_level(self.breadth_severity(inc), families, nuke=inc.family == "nuke")
        if target <= self.threat.level:
            self.threat.last_signal = now if self.level_severity(inc) >= 0.35 else self.threat.last_signal
            return None
        change = self.threat.raise_to(target, now, f"{inc.headline()} (incident {inc.id})", self.cfg["levels.auto_max"])
        if change is None:
            return None
        return int(change[0]), int(change[1]), f"{inc.headline()} (incident {inc.id})"

    @staticmethod
    def breadth_severity(inc: Incident) -> float:
        """Severity for threat-level purposes: one spammer is handled by deleting and a timeout, not by raising the
        whole server's guard, so member-driven incidents count fully only once several accounts are involved."""
        if inc.family in ("nuke", "integration"):
            return inc.rule_severity
        return GuildEngine.level_severity(inc) * min(1.0, 0.25 + 0.15 * len(inc.users))

    @staticmethod
    def level_severity(inc: Incident) -> float:
        """The rule severity without LEVEL_NEUTRAL detectors (the same combination as Incident.rule_severity)."""
        if not any(d in LEVEL_NEUTRAL for d in inc.top):
            return inc.rule_severity
        scores = sorted((v for d, v in inc.top.items() if d not in LEVEL_NEUTRAL), reverse=True)[:5]
        return 1 - math.prod(1 - v for v in scores) if scores else 0.0

    def _tag(self, plans: list[PlannedAction], signals: list[Signal], inc: Optional[Incident] = None) -> list[PlannedAction]:
        for plan in plans:
            if plan.incident_id is None:
                owner = inc
                if owner is None and signals:
                    owner = self._incident_for(signals[0])
                plan.incident_id = owner.id if owner else None
        return plans

    def tick(self, now: float) -> Outcome:
        out = Outcome()
        change = self.threat.tick(now, self.cfg["levels.decay_minutes"] * 60)
        if change:
            out.level_change = (int(change[0]), int(change[1]), "quiet period: stepping down")
        for inc in list(self.incidents.values()):
            if inc.status == "open" and now - inc.last >= INCIDENT_CLOSE_S:
                inc.status, inc.closed_at = "closed", now
                inc.note(now, "closed", "no new signals for 30 min")
                out.closed.append(inc)
                del self.incidents[inc.id]
        return out

    def set_level(self, level: Level, now: float, by: int, reason: str, hold_s: float = 3600) -> Outcome:
        old, new = self.threat.set_by_staff(level, now, by, reason, hold_s)
        out = Outcome(level_change=(int(old), int(new), f"set by staff: {reason}"))
        if new > old:
            open_incidents = sorted(self.incidents.values(), key=lambda i: i.last, reverse=True)
            inc = open_incidents[0] if open_incidents else None
            channels = inc.channels if inc else []
            out.plans += self._tag(self.planner.for_level(new, self.threat.orders, self.cfg, now,
                                                          inc.id if inc else f"level-{int(now)}", channels), [], inc)
        return out

    def incident_features(self, now: float):
        return self.features.vector(now)

    def model_incident_signal(self, now: float, probability: float, family: str, confident: bool) -> Outcome:
        """The incident model's periodic view (advisory). Only a confident view becomes a signal."""
        if not confident or probability < self.incident_bar:
            return Outcome()
        # a reading on its own only opens a (digest) incident when very confident; at 0.8+ it adds to open ones
        sig = Signal("incident_model", family if family in ("raid", "spam", "phishing", "nuke", "integration") else "raid",
                     0.5, probability, now, [Reason.of("MODEL_INCIDENT", family=family, confidence=round(probability, 2))],
                     source="model", evidence={"family": family, "p": round(probability, 3)})
        return self._absorb([sig], now, allow_new=probability >= max(0.9, self.incident_bar))

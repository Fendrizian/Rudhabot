"""Signals: the structured evidence a detector emits. A signal is an observation, never a verdict."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from rudhabot.security.reasons import explain

#: Incident families. A signal belongs to one; an incident groups signals of one family close in time.
FAMILIES = ("raid", "spam", "phishing", "nuke", "integration", "evasion")


@dataclass(frozen=True)
class Reason:
    code: str
    facts: tuple[tuple[str, object], ...] = ()

    @classmethod
    def of(cls, code: str, **facts) -> "Reason":
        return cls(code, tuple(sorted(facts.items())))

    def text(self) -> str:
        return explain(self.code, **dict(self.facts))

    def to_dict(self) -> dict:
        return {"code": self.code, "facts": {k: v for k, v in self.facts}}


@dataclass
class Signal:
    detector: str                      # which detector produced it (join_rate, flood, antinuke, ...)
    family: str                        # one of FAMILIES
    severity: float                    # how bad if true, 0..1
    confidence: float                  # how sure the detector is, 0..1
    ts: float
    reasons: list[Reason] = field(default_factory=list)
    users: list[int] = field(default_factory=list)          # accounts involved (authors, joiners, actors)
    channels: list[int] = field(default_factory=list)
    messages: list[tuple[int, int]] = field(default_factory=list)   # (channel_id, message_id) to act on
    actor: Optional[int] = None        # for audit-log signals: who made the change
    evidence: dict = field(default_factory=dict)            # counts, fingerprints, ids (never full text)
    snippet: Optional[str] = None      # at most 200 chars of a flagged message; kept 14 days
    source: str = "rule"               # rule | model

    @property
    def score(self) -> float:
        return self.severity * self.confidence

    def summary(self) -> str:
        return "; ".join(r.text() for r in self.reasons) or self.detector

    def to_dict(self) -> dict:
        return {"detector": self.detector, "family": self.family, "severity": round(self.severity, 3),
                "confidence": round(self.confidence, 3), "ts": self.ts, "reasons": [r.to_dict() for r in self.reasons],
                "users": self.users[:50], "channels": self.channels[:20], "messages": self.messages[:50],
                "actor": self.actor, "evidence": self.evidence, "source": self.source}

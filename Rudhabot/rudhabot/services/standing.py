"""Member standing: one view of a member across every system, so each system can treat people in proportion.

Inputs are this server's own records only: how long the member has been here, on how many days they posted (daily
message *counts*, never content), their moderation cases, the security incidents they were part of (and staff's
verdicts on them) and confirmed anomaly-monitor signals. No identity attributes, no message text, nothing from other
servers.

    score 0-100 -> band   new (<25) · regular (25-49) · established (50-74) · trusted (75+)
                          restricted: on probation or quarantined right now (whatever the score)

"trusted" also needs 30+ days in the server and 14+ active days, so it can't be earned in a weekend.

Where it is used
    security   automatic timeouts and probation skip trusted members; staff get a proposal instead (their messages
               are still removed if they post a scam link: compromised accounts are real)
    alerts     an alert about an established or trusted member says "possibly a compromised account"
    /modlog    the member file: standing and why, cases, incidents, anomaly signals, economy holds
"""
from __future__ import annotations

import calendar
import time
from dataclasses import dataclass, field
from datetime import timezone
from typing import Optional

TRUST_MIN_TENURE_DAYS = 30
TRUST_MIN_ACTIVE_DAYS = 14
ACTIVITY_WINDOW_DAYS = 90
CASE_WINDOW_DAYS = 180
CASE_WEIGHT = {"warn": 8, "timeout": 12, "kick": 20, "quarantine": 25, "ban": 40}
BANDS = (("trusted", 75), ("established", 50), ("regular", 25), ("new", 0))
BAND_ICONS = {"trusted": "🛡️", "established": "🟢", "regular": "🔵", "new": "🌱", "restricted": "⛔"}
CACHE_S = 600


@dataclass(frozen=True)
class Standing:
    user_id: int
    score: int
    band: str
    tenure_days: Optional[float]
    active_days: int
    messages: int
    restricted: Optional[str] = None
    factors: tuple[str, ...] = ()
    cases: tuple[dict, ...] = ()
    incidents: tuple[dict, ...] = ()
    signals: tuple[dict, ...] = ()

    @property
    def icon(self) -> str:
        return BAND_ICONS[self.band]

    @property
    def trusted(self) -> bool:
        return self.band == "trusted"

    @property
    def established(self) -> bool:
        return self.band in ("established", "trusted")

    def line(self) -> str:
        return f"{self.icon} **{self.band}** · {self.score}/100"


@dataclass
class StandingService:
    security: object                 # SecurityService
    reviews: object                  # ReviewService
    clock: object
    _cache: dict = field(default_factory=dict)

    def now(self) -> float:
        return self.clock.now(timezone.utc).timestamp()

    # ------------------------------------------------------------------ the score
    def of(self, guild_id: int, user_id: int, joined_at: Optional[float] = None) -> Standing:
        now = self.now()
        store = self.security.store
        if joined_at is None:
            rt = self.security.runtime(guild_id)
            member = rt.port.member(user_id) if rt is not None else None
            joined_at = getattr(member, "joined_at", None)
        since_day = time.strftime("%Y-%m-%d", time.gmtime(now - ACTIVITY_WINDOW_DAYS * 86400))
        act = store.activity(guild_id, user_id, since_day)
        if joined_at is None and act["first_day"]:
            joined_at = calendar.timegm(time.strptime(act["first_day"], "%Y-%m-%d"))
        tenure = max(0.0, (now - joined_at) / 86400) if joined_at else None
        factors = []
        score = 30 * min(tenure or 0.0, 365) / 365 + 45 * min(act["active_days"], 45) / 45
        if tenure is not None:
            factors.append(f"in the server {int(tenure)} day(s)")
        factors.append(f"posted on {act['active_days']} of the last {ACTIVITY_WINDOW_DAYS} days")

        penalty = 0.0
        cases = [c for c in store.cases_for(guild_id, user_id, 50) if now - c["created"] <= CASE_WINDOW_DAYS * 86400
                 and c["kind"] in CASE_WEIGHT and c.get("status") != "void"]
        for c in cases:
            weight = CASE_WEIGHT[c["kind"]] * (1 - (now - c["created"]) / (CASE_WINDOW_DAYS * 86400))
            penalty += weight * (0.5 if (c.get("extra") or {}).get("automatic") else 1.0)
        if cases:
            factors.append(f"{len(cases)} moderation case(s) in 6 months")
        incidents = store.incidents_for(guild_id, user_id, now - ACTIVITY_WINDOW_DAYS * 86400)
        counted = [i for i in incidents if i["verdict"] != "false"]
        penalty += sum(15 if i["verdict"] == "real" else 5 for i in counted)
        if counted:
            real = sum(1 for i in counted if i["verdict"] == "real")
            factors.append(f"in {len(counted)} security incident(s)" + (f", {real} judged real" if real else ""))
        signals = self.signals_for(user_id)
        confirmed = [s for s in signals if s["status"] == "confirmed"]
        penalty += 10 * len(confirmed)
        if confirmed:
            factors.append(f"{len(confirmed)} confirmed economy anomaly signal(s)")
        if penalty == 0:                     # a clean record counts for more the longer it has been kept
            score += 25 * min(tenure or 0.0, 60) / 60
            factors.append("clean record")
        score = int(round(max(0.0, min(100.0, score - penalty))))

        band = next(name for name, floor in BANDS if score >= floor)
        if band == "trusted" and ((tenure or 0) < TRUST_MIN_TENURE_DAYS or act["active_days"] < TRUST_MIN_ACTIVE_DAYS):
            band = "established"
        restricted = None
        if store.quarantined(guild_id, user_id):
            restricted = "quarantined"
        elif store.on_probation(guild_id, user_id):
            restricted = "on probation"
        if restricted:
            band = "restricted"
        return Standing(user_id, score, band, tenure, act["active_days"], act["messages"], restricted, tuple(factors),
                        tuple(cases[:10]), tuple(incidents[:10]), tuple(signals[:10]))

    def signals_for(self, user_id: int) -> list[dict]:
        if self.reviews is None:
            return []
        return [s for s in self.reviews.signals(status=None, limit=1000) if s["user"] == str(user_id)][:20]

    # ------------------------------------------------------------------ what other systems ask
    def trusted(self, guild_id: int, user_id: int) -> bool:
        """Cached for a few minutes: asked on the hot path of automatic responses."""
        now = self.now()
        hit = self._cache.get((guild_id, user_id))
        if hit is not None and now - hit[0] < CACHE_S:
            return hit[1]
        try:
            # fast path for raids (hundreds of brand-new targets at once): too new, or not here, is never trusted
            rt = self.security.runtime(guild_id)
            member = rt.port.member(user_id) if rt is not None else None
            joined = getattr(member, "joined_at", None)
            if joined is None or now - joined < TRUST_MIN_TENURE_DAYS * 86400:
                value = False
            else:
                value = self.of(guild_id, user_id, joined).trusted
        except Exception:
            value = False
        self._cache[(guild_id, user_id)] = (now, value)
        if len(self._cache) > 5000:
            for key in sorted(self._cache, key=lambda k: self._cache[k][0])[:1000]:
                del self._cache[key]
        return value

    def protect(self, guild_id: int, user_id: int) -> Optional[str]:
        """Why an automatic restriction should be left to staff for this member, or None."""
        return "a trusted member (left to staff)" if self.trusted(guild_id, user_id) else None

    def cross_refs_for_incident(self, guild_id: int, incident: dict, limit: int = 5) -> list[str]:
        """Lines linking a security alert to what other systems know about the members in it."""
        lines = []
        for user_id in (incident.get("users") or [])[:limit]:
            try:
                st = self.of(guild_id, int(user_id))
            except Exception:
                continue
            if st.established:
                lines.append(f"<@{user_id}> is {st.band} here ({st.score}/100): possibly a compromised account")
            open_signals = [s for s in st.signals if s["status"] in ("open", "confirmed")]
            if open_signals:
                lines.append(f"<@{user_id}> also has {len(open_signals)} economy anomaly signal(s) "
                             f"(`/review`: {', '.join(s['id'] for s in open_signals[:3])})")
        return lines

    def cross_refs_for_signal(self, guild_id: int, signal: dict) -> list[str]:
        """Lines linking an anomaly-monitor signal to the member's standing and security record."""
        try:
            st = self.of(guild_id, int(signal["user"]))
        except Exception:
            return []
        lines = [f"Standing: {st.line()}" + (f" ({st.restricted})" if st.restricted else "")]
        live = [i for i in st.incidents if i["verdict"] != "false"]
        if live:
            lines.append(f"Security: {len(live)} incident(s) in 90 days ("
                         + ", ".join(f"`{i['id']}`" for i in live[:3]) + ")")
        if st.cases:
            lines.append(f"Moderation: {len(st.cases)} case(s) in 6 months")
        return lines

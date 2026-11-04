"""Anti-nuke: per-account budgets for high-impact changes, read live from the audit log.

Every audit-log action has a weight (deleting a channel 3 points, a ban 1, granting @everyone Administrator enough to
exceed any budget at once). Each account may spend a budget of points per window that depends on its clearance tier
and the threat level. Going over the budget is the one case where Rudhabot acts on a staff account without asking:
the account is quarantined (its roles with dangerous permissions are removed; Discord never lets a bot do this to the
server owner), and anything it added in the same window that can do further damage (webhooks, bots, dangerous
permission grants to @everyone) is removed or undone. Everything is recorded and reversible by an Admin.

Rudhabot's own actions are never counted, and accounts on the anti-nuke allowlist (with an expiry) are exempt.
Raid clean-up should go through Rudhabot's own bulk ban, which doesn't count against anyone.

StaffBaseline (one of Clairvoyance's models, advisory): each account's usual rate of high-impact actions, so an account
acting far outside its *own* pattern is flagged even while still under budget.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

from rudhabot.security import perms as P
from rudhabot.security.clearance import Tier
from rudhabot.security.detectors import poisson_tail
from rudhabot.security.events import AuditEntry
from rudhabot.security.policy import SecurityConfig
from rudhabot.security.reasons import duration
from rudhabot.security.signals import Reason, Signal

WEIGHTS: dict[str, float] = {
    "channel_delete": 3, "role_delete": 3, "member_prune": 8, "ban": 1, "kick": 1, "webhook_create": 2,
    "webhook_update": 0.5, "webhook_delete": 0.5, "bot_add": 3, "integration_create": 2, "integration_delete": 1,
    "channel_create": 1, "role_create": 1, "emoji_delete": 1, "sticker_delete": 1, "soundboard_sound_delete": 1,
    "thread_delete": 0.5, "message_bulk_delete": 1, "invite_delete": 0.25, "unban": 0.5,
    "scheduled_event_delete": 0.5, "automod_rule_delete": 4, "automod_rule_update": 1, "channel_update": 0.25,
    "role_update": 0.25, "overwrite_create": 0.25, "overwrite_update": 0.25, "overwrite_delete": 0.5,
    "member_role_update": 0.25, "guild_update": 1, "app_command_permission_update": 1,
    "onboarding_update": 1, "onboarding_prompt_delete": 1,
}
TRACKED = frozenset(WEIGHTS)
BROAD_ROLE_MEMBERS = 25           # a role held by at least this many members counts as "broad"


@dataclass
class _Spend:
    ts: float
    action: str
    points: float
    target: Optional[int]


@dataclass
class ActorWindow:
    spends: deque = field(default_factory=lambda: deque(maxlen=500))
    warned_at: float = 0.0
    tripped_at: float = 0.0

    def total(self, now: float, span: float) -> float:
        while self.spends and now - self.spends[0].ts > span:
            self.spends.popleft()
        return sum(s.points for s in self.spends)

    def targets(self, action: str, now: float, span: float) -> list[int]:
        return [s.target for s in self.spends if s.action == action and now - s.ts <= span and s.target]


class AntiNuke:
    def __init__(self, bot_id: int = 0):
        self.bot_id = bot_id
        self.actors: dict[int, ActorWindow] = defaultdict(ActorWindow)
        self.role_members: dict[int, int] = {}        # role id -> member count (kept fresh by the service)
        self.role_perms: dict[int, int] = {}          # role id -> permission bits
        self.everyone_id: int = 0
        self.baseline = StaffBaseline()

    # ------------------------------------------------------------------ budgets
    @staticmethod
    def budget(cfg: SecurityConfig, tier: int, factor: float = 1.0) -> float:
        base = (cfg["antinuke.budget_admin"] if tier >= Tier.ADMIN else
                cfg["antinuke.budget_senior"] if tier == Tier.SENIOR else cfg["antinuke.budget_mod"])
        return max(1.0, base * factor)

    def weight(self, entry: AuditEntry) -> tuple[float, list[Signal]]:
        """Points for this entry, plus any escalation signals it raises on its own."""
        w = WEIGHTS.get(entry.action, 0.0)
        signals = []
        actor = f"<@{entry.actor_id}>"
        if entry.action == "role_update" and entry.perms_after is not None:
            gained = entry.perms_after & ~(entry.perms_before or 0)
            dangerous = gained & P.DANGEROUS
            if dangerous:
                broad = entry.target_is_everyone or self.role_members.get(entry.target_id or 0, 0) >= BROAD_ROLE_MEMBERS
                critical = bool(gained & P.CRITICAL)
                if broad:
                    target = "@everyone" if entry.target_is_everyone else f"<@&{entry.target_id}>"
                    signals.append(Signal("escalation", "nuke", 0.95 if critical else 0.7, 0.95, entry.ts,
                                          [Reason.of("PERM_ESCALATION_BROAD", actor=actor, target=target,
                                                     permissions=", ".join(P.names(dangerous)[:4]))],
                                          users=[entry.actor_id], actor=entry.actor_id,
                                          evidence={"role": entry.target_id, "gained": dangerous,
                                                    "before": entry.perms_before, "everyone": entry.target_is_everyone}))
                    w = 1000.0 if critical else 6.0          # handing everyone the keys trips any budget at once
                else:
                    w = 3.0 if critical else 1.0
        elif entry.action == "member_role_update" and entry.roles_added:
            gained = 0
            for role_id in entry.roles_added:
                gained |= self.role_perms.get(role_id, 0)
            dangerous = gained & P.CRITICAL
            if dangerous:
                w = 3.0
                signals.append(Signal("escalation", "nuke", 0.6, 0.8, entry.ts,
                                      [Reason.of("PERM_ESCALATION_GRANT", actor=actor,
                                                 permissions=", ".join(P.names(dangerous)[:4]))],
                                      users=[entry.actor_id, entry.target_id or 0], actor=entry.actor_id,
                                      evidence={"member": entry.target_id, "roles": list(entry.roles_added)}))
        elif entry.action in ("overwrite_create", "overwrite_update") and entry.perms_after is not None:
            gained = entry.perms_after & ~(entry.perms_before or 0) & (P.CRITICAL | P.MENTION_EVERYONE)
            if gained and entry.target_is_everyone:
                w = 4.0
                signals.append(Signal("escalation", "nuke", 0.7, 0.9, entry.ts,
                                      [Reason.of("PERM_OVERWRITE_DANGEROUS", actor=actor, target="@everyone",
                                                 permissions=", ".join(P.names(gained)[:4]))],
                                      users=[entry.actor_id], actor=entry.actor_id, channels=[entry.target_id or 0],
                                      evidence={"channel": entry.target_id, "gained": gained}))
        elif entry.action == "guild_update":
            for setting, label in (("verification_level", "verification level"), ("mfa_level", "2FA for moderation"),
                                   ("explicit_content_filter", "explicit media filter")):
                before, after = entry.before.get(setting), entry.after.get(setting)
                if before is not None and after is not None and after < before:
                    w = max(w, 4.0)
                    signals.append(Signal("config_weakened", "nuke", 0.65, 0.95, entry.ts,
                                          [Reason.of("CONFIG_WEAKENED", actor=actor, setting=label, before=before,
                                                     after=after)],
                                          users=[entry.actor_id], actor=entry.actor_id,
                                          evidence={"setting": setting, "before": before, "after": after}))
            if "vanity_url_code" in entry.after:
                w = max(w, 4.0)
        elif entry.action == "automod_rule_delete" or (entry.action == "automod_rule_update"
                                                        and entry.after.get("enabled") is False):
            signals.append(Signal("automod_removed", "nuke", 0.55, 0.95, entry.ts,
                                  [Reason.of("AUTOMOD_REMOVED", actor=actor)], users=[entry.actor_id],
                                  actor=entry.actor_id, evidence={"rule": entry.target_id}))
            w = max(w, 3.0)
        elif entry.action == "bot_add":
            severity = 0.6 if entry.actor_tier < Tier.ADMIN else 0.35
            signals.append(Signal("bot_added", "integration", severity, 0.9, entry.ts,
                                  [Reason.of("BOT_ADDED", actor=actor)], users=[entry.actor_id, entry.target_id or 0],
                                  actor=entry.actor_id, evidence={"bot": entry.target_id}))
        elif entry.action == "webhook_create":
            severity = 0.4 if entry.actor_tier < Tier.SENIOR else 0.2
            signals.append(Signal("webhook_created", "integration", severity, 0.8, entry.ts,
                                  [Reason.of("WEBHOOK_CREATED", actor=actor)], users=[entry.actor_id],
                                  actor=entry.actor_id, evidence={"webhook": entry.target_id}))
        return w, signals

    def observe(self, entry: AuditEntry, cfg: SecurityConfig, budget_factor: float = 1.0,
                exempt: bool = False) -> list[Signal]:
        if entry.action not in TRACKED or entry.actor_id in (0, self.bot_id):
            return []
        points, signals = self.weight(entry)
        if exempt or not cfg["antinuke.enabled"]:
            return [s for s in signals if s.detector in ("bot_added", "webhook_created")] if not exempt else []
        span = float(cfg["antinuke.window_s"])
        win = self.actors[entry.actor_id]
        win.spends.append(_Spend(entry.ts, entry.action, points, entry.target_id))
        used = win.total(entry.ts, span)
        tier = Tier.MODERATOR if entry.actor_bot else entry.actor_tier
        budget = self.budget(cfg, tier, budget_factor)
        actor = f"<@{entry.actor_id}>"
        counts: dict[str, int] = defaultdict(int)
        for s in win.spends:
            counts[s.action] += 1
        summary = ", ".join(f"{n}x {a.replace('_', ' ')}" for a, n in sorted(counts.items(), key=lambda x: -x[1])[:4])
        if used > budget and entry.ts - win.tripped_at > span:
            win.tripped_at = entry.ts
            signals.append(Signal("nuke_budget", "nuke", 0.97, 0.95, entry.ts,
                                  [Reason.of("NUKE_BUDGET", actor=actor, count=len(win.spends), window=duration(span),
                                             actions=summary, budget=round(budget))],
                                  users=[entry.actor_id], actor=entry.actor_id,
                                  evidence={"points": round(used, 2), "budget": round(budget, 2), "tier": int(tier),
                                            "actions": dict(counts), "bot": entry.actor_bot}))
        elif used >= 0.7 * budget and entry.ts - win.warned_at > span and entry.ts - win.tripped_at > span:
            win.warned_at = entry.ts
            signals.append(Signal("nuke_warning", "nuke", 0.45, 0.8, entry.ts,
                                  [Reason.of("NUKE_WARNING", actor=actor, used=round(used, 1), budget=round(budget),
                                             window=duration(span))],
                                  users=[entry.actor_id], actor=entry.actor_id,
                                  evidence={"points": round(used, 2), "budget": round(budget, 2)}))
        if cfg["models.staff_baseline"]:
            unusual = self.baseline.observe(entry.actor_id, entry.ts, points, span)
            if unusual is not None:
                signals.append(unusual)
        return signals

    def added_by(self, actor_id: int, action: str, now: float, span: float) -> list[int]:
        """Targets (webhook ids, bot ids, ...) of `action` by this actor within the window, for containment."""
        return self.actors[actor_id].targets(action, now, span) if actor_id in self.actors else []


class StaffBaseline:
    """Each account's usual high-impact points per hour (EWMA over active hours, prior 1 point/hour). A window whose
    points are wildly improbable under that account's own Poisson rate is flagged (p < 1e-4, at least 6 points)."""

    def __init__(self, alpha: float = 0.1, prior: float = 1.0):
        self.alpha, self.prior = alpha, prior
        self.rate: dict[int, float] = {}
        self.hours: dict[int, int] = {}
        self._current: dict[int, tuple[int, float]] = {}
        self._window: dict[int, deque] = defaultdict(lambda: deque(maxlen=500))
        self._flagged: dict[int, float] = {}

    def observe(self, actor: int, ts: float, points: float, span: float) -> Optional[Signal]:
        hour = int(ts // 3600)
        cur_hour, cur_points = self._current.get(actor, (hour, 0.0))
        if cur_hour != hour:
            self._fold(actor, cur_points)
            cur_points = 0.0
        self._current[actor] = (hour, cur_points + points)
        win = self._window[actor]
        win.append((ts, points))
        while win and ts - win[0][0] > span:
            win.popleft()
        total = sum(p for _, p in win)
        expected = max(self.rate.get(actor, self.prior), 0.25) * span / 3600
        if total < 6 or ts - self._flagged.get(actor, 0) < span:
            return None
        p = poisson_tail(int(math.floor(total)), expected)
        if p >= 1e-4:
            return None
        self._flagged[actor] = ts
        return Signal("staff_unusual", "nuke", 0.5, min(0.9, 0.5 + 0.04 * -math.log10(max(p, 1e-30))), ts,
                      [Reason.of("STAFF_UNUSUAL", actor=f"<@{actor}>",
                                 detail=f"{total:g} points in {duration(span)} vs about {expected:.1f} usually")],
                      users=[actor], actor=actor, source="model",
                      evidence={"points": total, "expected": round(expected, 3), "p_value": p,
                                "hours_seen": self.hours.get(actor, 0)})

    def _fold(self, actor: int, points: float) -> None:
        old = self.rate.get(actor, self.prior)
        self.rate[actor] = (1 - self.alpha) * old + self.alpha * points
        self.hours[actor] = self.hours.get(actor, 0) + 1

    def to_dict(self) -> dict:
        return {"rate": {str(k): round(v, 4) for k, v in self.rate.items()},
                "hours": {str(k): v for k, v in self.hours.items()}}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "StaffBaseline":
        out = cls()
        if d:
            out.rate = {int(k): float(v) for k, v in d.get("rate", {}).items()}
            out.hours = {int(k): int(v) for k, v in d.get("hours", {}).items()}
        return out

"""Proportionate responses: turn signals (plus the threat level's standing orders) into planned actions.

Authority of a planned action:
    auto      carried out by Rudhabot in live mode (reported only in shadow mode). Only low-impact, reversible kinds:
              deleting the flagged messages, slowmode (lifted automatically), probation (lifted automatically),
              timeouts of at most 10 minutes; plus anti-nuke containment of an account over its budget.
    confirm   proposed to staff with a button; one member of the required tier carries it out.
    approve2  needs a requester *and* a different approver of the required tier (lockdowns, restores).
    alert     nothing to do but tell staff.

Planning never consults a model on its own: a model signal can only make an alert more prominent, or (when
`auto.model_corroborates` is on) be the second piece of evidence next to a deterministic link finding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from rudhabot.security.clearance import Tier
from rudhabot.security.levels import Level, StandingOrders
from rudhabot.security.policy import SecurityConfig
from rudhabot.security.signals import Signal

AUTO_KINDS = frozenset({"delete_messages", "slowmode", "probation", "timeout", "quarantine", "delete_webhook",
                        "kick_bot", "revert_permissions"})
PROPOSAL_KINDS = frozenset({"timeout", "kick", "ban", "pause_invites", "pause_dms", "lockdown", "restore",
                            "revert_permissions"})
MAX_AUTO_TIMEOUT_S = 600


@dataclass
class PlannedAction:
    kind: str
    authority: str                      # auto | confirm | approve2 | alert
    tier: Tier                          # clearance needed to carry it out / approve it
    reason: str                         # plain-language summary
    targets: list[int] = field(default_factory=list)          # users, channels, webhooks or bots, by kind
    params: dict = field(default_factory=dict)
    codes: list[str] = field(default_factory=list)
    incident_id: Optional[str] = None
    key: str = ""                       # de-duplication key

    def to_dict(self) -> dict:
        return {"kind": self.kind, "authority": self.authority, "tier": int(self.tier), "reason": self.reason,
                "targets": self.targets[:200], "params": self.params, "codes": self.codes,
                "incident_id": self.incident_id}


class Planner:
    """Stateless apart from de-duplication: the same action on the same target isn't planned twice within its
    cooldown (one slowmode per channel per 2 min, one probation per member, one proposal per incident and kind...)."""

    def __init__(self):
        self._recent: dict[str, float] = {}
        self.deleted_by: dict[int, list[float]] = {}         # author -> times their messages were auto-deleted

    def _fresh(self, key: str, now: float, cooldown: float) -> bool:
        last = self._recent.get(key)
        if last is not None and now - last < cooldown:
            return False
        self._recent[key] = now
        if len(self._recent) > 20000:
            for k in sorted(self._recent, key=self._recent.get)[:5000]:
                del self._recent[k]
        return True

    def note_deletion(self, author: int, now: float) -> int:
        times = [t for t in self.deleted_by.get(author, []) if now - t <= 600] + [now]
        self.deleted_by[author] = times[-20:]
        return len(times)

    # ------------------------------------------------------------------ messages
    def for_message(self, signals: list[Signal], delete: bool, cfg: SecurityConfig, orders: StandingOrders,
                    now: float, author: int, channel: int, member_age_days: Optional[float],
                    model_scam: float = 0.0, author_tier: int = 0) -> list[PlannedAction]:
        plans: list[PlannedAction] = []
        codes = [r.code for s in signals for r in s.reasons]
        link_sig = next((s for s in signals if s.detector == "link"), None)
        if not delete and link_sig is not None and cfg["auto.model_corroborates"] and model_scam >= 0.97 \
                and link_sig.severity >= 0.5:
            delete = True                       # deterministic link finding + a very confident model
            codes.append("MSG_MODEL_SCAM")
        if not delete and orders.remove_new_member_links and link_sig is not None and member_age_days is not None \
                and member_age_days < 1 and author_tier == 0:
            delete = True
        if delete and cfg["auto.delete_spam"]:
            refs = sorted({m for s in signals for m in s.messages})
            if refs:
                plans.append(PlannedAction("delete_messages", "auto", Tier.HELPER, _summary(signals), targets=[author],
                                           params={"messages": refs[-100:]}, codes=codes))
                if author_tier == 0:
                    strikes = self.note_deletion(author, now)
                    if strikes >= 2 and orders.timeout_repeat_spammers and cfg["auto.timeouts"] \
                            and self._fresh(f"timeout:{author}", now, 600):
                        minutes = min(int(cfg["auto.timeout_minutes"]), MAX_AUTO_TIMEOUT_S // 60)
                        plans.append(PlannedAction("timeout", "auto", Tier.HELPER,
                                                   f"repeat spam: {strikes} removals in 10 min", targets=[author],
                                                   params={"seconds": minutes * 60},
                                                   codes=codes + ["MSG_REPEAT_OFFENDER"]))
        for s in signals:
            if s.detector == "coordinated" and s.evidence.get("actionable") and len(s.users) >= 5 and self._fresh(f"coord-ban:{s.evidence.get('fingerprint')}", now, 3600):
                plans.append(PlannedAction("ban", "confirm", Tier.SENIOR,
                                           f"{len(s.users)} accounts posted near-identical messages", targets=list(s.users),
                                           params={"delete_seconds": 3600}, codes=[r.code for r in s.reasons]))
            if s.detector == "mention_bomb" and author_tier == 0 and cfg["auto.timeouts"] \
                    and (member_age_days is None or member_age_days < 7) and self._fresh(f"timeout:{author}", now, 600):
                plans.append(PlannedAction("timeout", "auto", Tier.HELPER, "mass mentions from a new member",
                                           targets=[author], params={"seconds": MAX_AUTO_TIMEOUT_S},
                                           codes=["MSG_MENTION_BOMB"]))
        surge = next((s for s in signals if s.detector == "channel_surge"), None)
        if surge is not None and orders.slowmode_seconds and cfg["auto.slowmode"] and not cfg.protected(channel) \
                and self._fresh(f"slowmode:{channel}", now, 120):
            plans.append(PlannedAction("slowmode", "auto", Tier.HELPER, surge.summary(), targets=[channel],
                                       params={"seconds": orders.slowmode_seconds, "revert_after": 900},
                                       codes=["MSG_CHANNEL_SURGE"]))
        if any(s.detector == "webhook_flood" for s in signals):
            hook = next(s for s in signals if s.detector == "webhook_flood").evidence.get("webhook_id")
            if hook and self._fresh(f"webhook:{hook}", now, 3600):
                plans.append(PlannedAction("delete_webhook", "auto", Tier.SENIOR, "a webhook is flooding a channel",
                                           targets=[hook], codes=["WEBHOOK_FLOOD"]))
        return plans

    # ------------------------------------------------------------------ joins
    def for_join(self, signals: list[Signal], cfg: SecurityConfig, orders: StandingOrders, now: float,
                 user_id: int, account_age_days: float) -> list[PlannedAction]:
        plans = []
        if not cfg["probation_role"] or not cfg["auto.probation"]:
            return plans
        reasons = [s for s in signals if s.detector in ("join_cohort", "join_surge", "spammer_flag")]
        new_account = account_age_days < cfg["joins.new_account_days"]
        why = None
        if any(s.detector == "join_cohort" and user_id in s.users for s in reasons):
            why, code = "joined as part of a look-alike group", "JOIN_COHORT"
        elif any(s.detector == "spammer_flag" for s in reasons):
            why, code = "Discord flags this account as a likely spammer", "JOIN_SPAMMER_FLAG"
        elif orders.probation_all_joins:
            why, code = "standing order: every new join is on probation at this threat level", "JOIN_SURGE"
        elif orders.probation_new_accounts and new_account:
            why, code = "standing order: new accounts are on probation at this threat level", "JOIN_NEW_ACCOUNT"
        if why and self._fresh(f"probation:{user_id}", now, 86400):
            plans.append(PlannedAction("probation", "auto", Tier.HELPER, why, targets=[user_id],
                                       params={"minutes": int(cfg["probation_minutes"])}, codes=[code]))
        # members of a confirmed cohort *already* in the server get probation too (once each)
        for s in signals:
            if s.detector == "join_cohort":
                rest = [u for u in s.users if u != user_id and self._fresh(f"probation:{u}", now, 86400)]
                if rest:
                    plans.append(PlannedAction("probation", "auto", Tier.HELPER, "joined as part of a look-alike group",
                                               targets=rest, params={"minutes": int(cfg["probation_minutes"])},
                                               codes=["JOIN_COHORT"]))
        return plans

    # ------------------------------------------------------------------ audit log (anti-nuke)
    def for_audit(self, signals: list[Signal], cfg: SecurityConfig, now: float, actor_tier: int,
                  contained_targets: dict) -> list[PlannedAction]:
        plans = []
        for s in signals:
            if s.detector != "nuke_budget" or s.actor is None:
                continue
            actor = s.actor
            if cfg["antinuke.quarantine"] and actor_tier < Tier.OWNER and self._fresh(f"quarantine:{actor}", now, 1800):
                plans.append(PlannedAction("quarantine", "auto", Tier.SENIOR, s.summary(), targets=[actor],
                                           params={"bot": bool(s.evidence.get("bot"))}, codes=["NUKE_BUDGET"]))
            if cfg["antinuke.contain"]:
                for hook in contained_targets.get("webhook_create", []):
                    if self._fresh(f"webhook:{hook}", now, 3600):
                        plans.append(PlannedAction("delete_webhook", "auto", Tier.SENIOR,
                                                   "created by an account that went over its anti-nuke budget",
                                                   targets=[hook], codes=["NUKE_BUDGET"]))
                for bot in contained_targets.get("bot_add", []):
                    if self._fresh(f"kickbot:{bot}", now, 3600):
                        plans.append(PlannedAction("kick_bot", "auto", Tier.SENIOR,
                                                   "added by an account that went over its anti-nuke budget",
                                                   targets=[bot], codes=["NUKE_BUDGET"]))
                for grant in contained_targets.get("escalations", []):
                    if self._fresh(f"revert:{grant['role']}", now, 600):
                        plans.append(PlannedAction("revert_permissions", "auto", Tier.ADMIN,
                                                   "undo dangerous permissions given to everyone",
                                                   targets=[grant["role"]],
                                                   params={"remove": grant["gained"]}, codes=["PERM_ESCALATION_BROAD"]))
            if self._fresh(f"restore:{actor}", now, 3600):
                plans.append(PlannedAction("restore", "approve2" if cfg["approvals.two_person"] else "confirm",
                                           Tier.ADMIN, "review what changed and restore roles/channels from the "
                                           "latest snapshot", targets=[actor], codes=["NUKE_BUDGET"]))
        for s in signals:
            if s.detector == "escalation" and s.evidence.get("everyone") is not None and not any(
                    p.kind == "revert_permissions" for p in plans) and self._fresh(f"revert-offer:{s.evidence.get('role')}", now, 600):
                plans.append(PlannedAction("revert_permissions", "confirm", Tier.ADMIN,
                                           "undo the dangerous permissions just granted", targets=[s.evidence["role"]],
                                           params={"remove": s.evidence.get("gained", 0)},
                                           codes=[r.code for r in s.reasons]))
        return plans

    # ------------------------------------------------------------------ threat level (incident-wide)
    def for_level(self, level: Level, orders: StandingOrders, cfg: SecurityConfig, now: float, incident_id: str,
                  channels: list[int]) -> list[PlannedAction]:
        plans = []
        if orders.propose_pause_invites and self._fresh(f"pause_invites:{incident_id}", now, 86400):
            plans.append(PlannedAction("pause_invites", "confirm", Tier.SENIOR,
                                       f"threat level {level.label}: stop new joins for a while",
                                       params={"minutes": 60}, incident_id=incident_id))
        if orders.propose_pause_dms and self._fresh(f"pause_dms:{incident_id}", now, 86400):
            plans.append(PlannedAction("pause_dms", "confirm", Tier.SENIOR,
                                       f"threat level {level.label}: stop members DMing each other through the server",
                                       params={"minutes": 60}, incident_id=incident_id))
        if orders.propose_lockdown and self._fresh(f"lockdown:{incident_id}:{orders.propose_lockdown}", now, 86400):
            scope = "server" if orders.propose_lockdown == "server" or not channels else "channels"
            plans.append(PlannedAction("lockdown", "approve2" if cfg["approvals.two_person"] else "confirm",
                                       Tier.SENIOR, f"threat level {level.label}: lock "
                                       + ("the whole server" if scope == "server" else f"{len(channels)} affected channel(s)"),
                                       targets=[] if scope == "server" else channels[:25],
                                       params={"scope": scope, "minutes": int(cfg["lockdown.default_minutes"])},
                                       incident_id=incident_id))
        return plans


def _summary(signals: list[Signal]) -> str:
    texts = []
    for s in signals:
        text = s.summary()
        if text not in texts:
            texts.append(text)
    return "; ".join(texts[:3])

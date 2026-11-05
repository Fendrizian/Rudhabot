"""Replay simulated days through the real detection engine and score it, and collect training data for the incident
model and the alert ranker.

Per attack: was it alerted (an incident of severity >= 0.5 overlapping it), how fast, how high the threat level
went, how much of it the automatic plan would have contained (attack messages deleted, raid accounts on probation,
destructive actions before quarantine). Per normal day: false alerts, and automatic actions that would have hit
innocent members (messages deleted, probation, timeouts, quarantine).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from rudhabot.ml.security.sim import BOT, GUILD, Day, Server, label_at, schedule
from rudhabot.security.engine import GuildEngine
from rudhabot.security.events import AuditEntry, Message, Tick
from rudhabot.security.policy import SecurityConfig

ALERT = 0.5
DETECTORS = ("join_surge", "join_cohort", "spammer_flag", "ban_evasion", "flood", "duplicate", "coordinated",
             "mention_bomb", "mention_flood", "everyone_attempt", "invite", "link", "attachment", "channel_surge",
             "webhook_flood", "nuke_budget", "nuke_warning", "escalation", "config_weakened", "automod_removed",
             "bot_added", "webhook_created", "staff_unusual", "model_scam", "model_spam", "incident_model",
             "member_report")
RANK_FEATURES = tuple(f"top_{d}" for d in DETECTORS) + ("log_users", "log_channels", "log_signals", "level",
                                                       "rule_severity", "model_bonus") + tuple(
    f"fam_{f}" for f in ("raid", "spam", "phishing", "nuke", "integration", "evasion", "model"))


def sim_config(**overrides) -> SecurityConfig:
    values = {"mode": "live", "probation_role": 7, "channels.relaxed": [102]}
    values.update(overrides)
    return SecurityConfig(values)


def rank_vector(inc) -> np.ndarray:
    v = [inc.top.get(d, 0.0) for d in DETECTORS]
    v += [math.log1p(len(inc.users)), math.log1p(len(inc.channels)), math.log1p(sum(inc.counts.values())),
          inc.peak_level / 5, inc.rule_severity, inc.model_bonus / 0.15]
    v += [1.0 if inc.family == f else 0.0 for f in ("raid", "spam", "phishing", "nuke", "integration", "evasion", "model")]
    return np.asarray(v, dtype=np.float32)


@dataclass
class DayResult:
    features: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    attacks: list = field(default_factory=list)
    benign: dict = field(default_factory=lambda: defaultdict(int))
    alerts: list = field(default_factory=list)          # (rank vector, is_true_attack) for alerted incidents
    ranked: list = field(default_factory=list)          # (rank vector, is_true_attack, alerted) for every incident,
                                                        # read 5 minutes after it opened (when the digest lists it)
    model_flags: list = field(default_factory=list)     # (ts, p, label)


def run_day(engine: GuildEngine, day: Day, *, incident_model: Optional[Callable] = None,
            message_model: Optional[Callable] = None, collect: bool = True) -> DayResult:
    res = DayResult()
    attack_msgs = {m: a for a in day.attacks for m in a["messages"]}
    attack_accounts = {u: a for a in day.attacks for u in a["accounts"]}
    per_attack = {id(a): {"family": a["family"], "start": a["start"], "end": a["end"], "alerted_at": None,
                          "signal_at": None, "max_level": int(engine.level), "deleted": 0, "messages": len(a["messages"]),
                          "accounts": len(a["accounts"]), "probation": 0, "contained_after": None,
                          "actions": a["actions"], "actions_seen": 0} for a in day.attacks}
    for event in day.events:                     # latency is measured from the attack's first event
        for a in day.attacks:
            pa = per_attack[id(a)]
            if pa.get("first") is None and event.ts >= a["start"] - 1 and (getattr(event, "message_id", None) in a["messages"]
                                            or getattr(event, "user_id", None) in a["accounts"]
                                            or getattr(event, "actor_id", None) in a["accounts"]):
                pa["first"] = event.ts
    alerted: set[str] = set()
    ranked: set[str] = set()
    probationed: set[int] = set()
    next_minute = day.start + 60

    def attack_for(ts: float):
        for a in day.attacks:
            if a["start"] - 60 <= ts <= a["end"] + 600:
                return a
        return None

    def handle(out, ts):
        for s in out.signals:
            a = None
            if any(u in attack_accounts for u in s.users) or (s.actor and s.actor in attack_accounts):
                a = attack_accounts.get(s.actor) or next(attack_accounts[u] for u in s.users if u in attack_accounts)
            elif any(m in attack_msgs for _, m in s.messages):
                a = next(attack_msgs[m] for _, m in s.messages if m in attack_msgs)
            if a is not None and s.score >= 0.2 and per_attack[id(a)]["signal_at"] is None:
                per_attack[id(a)]["signal_at"] = ts
        for inc in list(engine.incidents.values()) + out.closed:
            if inc.id not in ranked and (ts - inc.opened >= 300 or inc in out.closed):
                ranked.add(inc.id)
                a = next((x for x in day.attacks if set(inc.users) & x["accounts"]), None) or (
                    attack_for(inc.opened) if inc.family == "model" or not inc.users else None)
                res.ranked.append((rank_vector(inc), a is not None, inc.id in alerted))
            if inc.id in alerted or inc.severity < ALERT:
                continue
            alerted.add(inc.id)
            users = set(inc.users)
            a = next((x for x in day.attacks if users & x["accounts"]), None) or attack_for(inc.opened)
            truth = a is not None and (bool(users & a["accounts"]) or inc.family == "model")
            res.alerts.append((rank_vector(inc), bool(truth)))
            if truth:
                pa = per_attack[id(a)]
                if pa["alerted_at"] is None:
                    pa["alerted_at"] = ts
            else:
                res.benign["false_alerts"] += 1
        for plan in out.plans:
            if plan.kind == "delete_messages":
                for _, m in plan.params.get("messages", []):
                    if m in attack_msgs:
                        per_attack[id(attack_msgs[m])]["deleted"] += 1
                        attack_msgs.pop(m)                     # count each message once
                    elif m not in attack_msgs and not any(m in a["messages"] for a in day.attacks):
                        res.benign["deleted_innocent"] += 1
            elif plan.kind in ("probation", "timeout", "quarantine", "kick_bot"):
                for u in plan.targets:
                    a = attack_accounts.get(u)
                    if a is None:
                        res.benign[f"{plan.kind}_innocent"] += 1
                    elif plan.kind == "probation" and u not in probationed:
                        probationed.add(u)
                        per_attack[id(a)]["probation"] += 1
                    elif plan.kind in ("quarantine", "kick_bot") and per_attack[id(a)]["contained_after"] is None:
                        per_attack[id(a)]["contained_after"] = per_attack[id(a)]["actions_seen"]
            elif plan.authority in ("confirm", "approve2") and attack_for(ts) is None:
                res.benign[f"proposal_{plan.kind}_benign"] += 1
        for pa in per_attack.values():
            if pa["start"] - 60 <= ts <= pa["end"] + 600:
                pa["max_level"] = max(pa["max_level"], int(engine.level))

    for event in day.events:
        while event.ts >= next_minute:
            out = engine.observe(Tick(GUILD, next_minute))
            handle(out, next_minute)
            if collect or incident_model is not None:
                vec = engine.incident_features(next_minute)
                label = label_at(day, next_minute)
                if collect:
                    res.features.append(vec)
                    res.labels.append(label)
                if incident_model is not None:
                    p, family, confident = incident_model(vec)
                    res.model_flags.append((next_minute, p, label))
                    handle(engine.model_incident_signal(next_minute, p, family, confident), next_minute)
            next_minute += 60
        if isinstance(event, AuditEntry) and event.actor_id in attack_accounts:
            per_attack[id(attack_accounts[event.actor_id])]["actions_seen"] += 1
        out = engine.observe(event)
        handle(out, event.ts)
        if message_model is not None and isinstance(event, Message) and out.model_candidate:
            scam, spam = message_model(event.content)
            handle(engine.observe_model(event, scam, spam), event.ts)
    res.attacks = list(per_attack.values())
    return res


def run_server(seed: int, days: int, *, families=None, attack_rate: float = 0.6, members: int = 1200,
               incident_model=None, message_model=None, collect: bool = True, config: Optional[SecurityConfig] = None,
               warmup_days: int = 2) -> list[DayResult]:
    """Simulate one server for `warmup_days` quiet days (baselines learn) and then `days` scored days."""
    import random
    from rudhabot.ml.security.sim import FAMILIES
    server = Server(seed, members=members)
    engine = GuildEngine(GUILD, config or sim_config(), bot_id=BOT)
    for _ in range(warmup_days):
        run_day(engine, server.simulate_day(()), collect=False)
    plan = schedule(random.Random(seed * 7 + 1), days, attack_rate, families or FAMILIES)
    return [run_day(engine, server.simulate_day(attacks), incident_model=incident_model, message_model=message_model,
                    collect=collect) for attacks in plan]


def summarise(results: list[DayResult]) -> dict:
    """Per-family detection and containment, plus false positives per simulated day."""
    by_family: dict[str, list] = defaultdict(list)
    for r in results:
        for a in r.attacks:
            by_family[a["family"]].append(a)
    days = len(results)
    out = {"days": days, "attacks": sum(len(v) for v in by_family.values()), "families": {}}
    for family, attacks in sorted(by_family.items()):
        n = len(attacks)
        alerted = [a for a in attacks if a["alerted_at"] is not None]
        latency = sorted(max(0.0, a["alerted_at"] - (a.get("first") or a["start"])) for a in alerted)
        msgs = sum(a["messages"] for a in attacks)
        entry = {"n": n, "alerted": len(alerted) / n, "signalled": sum(a["signal_at"] is not None for a in attacks) / n,
                 "median_latency_s": latency[len(latency) // 2] if latency else None,
                 "p90_latency_s": latency[int(len(latency) * 0.9)] if latency else None,
                 "mean_max_level": sum(a["max_level"] for a in attacks) / n}
        if msgs:
            entry["messages_deleted"] = sum(a["deleted"] for a in attacks) / msgs
        accounts = sum(a["accounts"] for a in attacks)
        if family in ("join_raid", "slow_drip"):
            entry["accounts_on_probation"] = sum(a["probation"] for a in attacks) / max(1, accounts)
        if family in ("staff_nuke", "malicious_bot"):
            contained = [a for a in attacks if a["contained_after"] is not None]
            entry["contained"] = len(contained) / n
            entry["median_actions_before_containment"] = (sorted(a["contained_after"] for a in contained)[len(contained) // 2]
                                                          if contained else None)
            entry["mean_actions"] = sum(a["actions"] for a in attacks) / n
        out["families"][family] = entry
    benign = defaultdict(int)
    for r in results:
        for k, v in r.benign.items():
            benign[k] += v
    out["per_day"] = {k: v / max(1, days) for k, v in sorted(benign.items())}
    quiet = [r for r in results if not r.attacks]
    out["quiet_days"] = len(quiet)
    out["false_alerts_per_quiet_day"] = sum(r.benign.get("false_alerts", 0) for r in quiet) / max(1, len(quiet))
    return out

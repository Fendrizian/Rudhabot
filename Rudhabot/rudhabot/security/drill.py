"""`/security drill`: rehearse an attack against this server's own settings, in a sandbox.

A simulated community (the wargame simulator) suffers the chosen attack; the events around it are replayed through a
fresh detection engine using a copy of the server's configuration with automation switched on. Nothing touches the
real server: the report says what would have been detected, how fast, and what Rudhabot would have done on its own
versus proposed to staff.
"""
from __future__ import annotations

import random
from collections import Counter

from rudhabot.security.policy import SecurityConfig
from rudhabot.security.reasons import duration

DESCRIPTIONS = {
    "join_raid": "look-alike young accounts join in a burst, many then spam",
    "spam_raid": "recently joined accounts flood channels with near-duplicates and pings",
    "phishing": "compromised members post scam links",
    "mention_bomb": "messages pinging dozens of members",
    "staff_nuke": "a compromised staff account deletes channels and roles and bans members",
    "malicious_bot": "an admin adds a bot that starts destroying channels",
    "webhook_spam": "a webhook floods @everyone messages",
    "slow_drip": "raid accounts trickle in over hours and post scam links later",
}


def run_drill(scenario: str, config: SecurityConfig, seed: int | None = None, models=None) -> str:
    """`models`: the bot's loaded SecurityModels (optional), so the drill includes Clairvoyance's readings exactly as
    the live system would (subject to the same per-model switches)."""
    from rudhabot.ml.security.sim import BOT, GUILD, Day, Server
    from rudhabot.ml.security.wargame import run_day
    from rudhabot.security.engine import GuildEngine
    seed = random.randrange(10**6) if seed is None else seed
    server = Server(seed, members=1200)
    day = server.simulate_day((scenario,), normal_events=())
    attack = day.attacks[0]
    lo, hi = attack["start"] - 1800, attack["end"] + 1800
    window = Day([e for e in day.events if lo <= e.ts <= hi], day.attacks, [], lo, day.members)
    values = config.to_dict()
    values.update({"mode": "live", "probation_role": values.get("probation_role") or 1})
    cfg = SecurityConfig(values, config.version)
    engine = GuildEngine(GUILD, cfg, bot_id=BOT)
    plans: Counter = Counter()
    proposals: Counter = Counter()
    original = engine.observe

    def observe(event):
        out = original(event)
        for p in out.plans:
            (plans if p.authority == "auto" else proposals)[p.kind] += max(1, len(p.targets))
        return out
    engine.observe = observe
    message_model = incident_model = None
    if models is not None and getattr(models, "message_ready", False) and cfg["models.message"]:
        def message_model(text):
            s = models.message_scores(text)
            return (0.0, 0.0) if s["uncertain"] else (s["scam"], s["spam"])
    if models is not None and getattr(models, "incident_ready", False) and cfg["models.incident"]:
        def incident_model(vec):
            s = models.incident_scores(vec)
            return s["p"], s["family"], s["confident"]
    result = run_day(engine, window, collect=False, message_model=message_model, incident_model=incident_model)
    a = result.attacks[0]
    lines = [f"**Scenario:** {DESCRIPTIONS.get(scenario, scenario)} "
             f"({a['accounts']} account(s), {a['messages']} message(s){', ' + str(a['actions']) + ' audit-log actions' if a['actions'] else ''}).",
             f"Settings: config v{config.version}, automation forced on for the drill; Clairvoyance "
             f"{'included' if message_model or incident_model else 'not loaded (rules only)'}."]
    if a["alerted_at"] is not None:
        start = a.get("first") or a["start"]
        lines.append(f"**Detected** {duration(max(0.0, a['alerted_at'] - start))} after it began.")
    elif a["signal_at"] is not None:
        lines.append("**Flagged** but below the alert bar: it would appear in the low-severity digest.")
    else:
        lines.append("**Not detected** by the rules in this run.")
    lines.append(f"**Threat level** reached {a['max_level']}.")
    auto = [f"{k.replace('_', ' ')} ×{v}" for k, v in plans.most_common()]
    lines.append("**Automatic (live mode):** " + (", ".join(auto) or "nothing"))
    if a["messages"]:
        lines.append(f"Attack messages removed: {a['deleted']}/{a['messages']}.")
    if a["accounts"] and scenario in ("join_raid", "slow_drip"):
        lines.append(f"Raid accounts on probation: {a['probation']}/{a['accounts']}.")
    if a["contained_after"] is not None:
        lines.append(f"Contained after {a['contained_after']} of {a['actions']} destructive actions.")
    asked = [f"{k.replace('_', ' ')} ×{v}" for k, v in proposals.most_common()]
    lines.append("**Proposed to staff:** " + (", ".join(asked) or "nothing"))
    benign = {k: v for k, v in result.benign.items() if v}
    lines.append("**Innocent members affected:** " + (", ".join(f"{k.replace('_', ' ')} {v}" for k, v in benign.items())
                                                      or "none"))
    lines.append("_Simulated community and attack; nothing on the real server was touched. In shadow mode the "
                 "automatic actions would only be reported._")
    return "\n".join(lines)

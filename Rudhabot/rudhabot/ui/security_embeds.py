"""Embeds for server security and moderation. Plain moderation language; the military flavour is only in
descriptions. Every alert says what was observed (reason codes), what was done (with its status) and how sure it is,
and ends with the reminder that signals are evidence, not proof."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import discord

from rudhabot.security.clearance import Tier
from rudhabot.security.levels import Level, LABELS
from rudhabot.security.reasons import explain
from rudhabot.ui.theme import Colors

LEVEL_COLORS = {1: Colors.THREAT_NORMAL, 2: Colors.THREAT_ELEVATED, 3: Colors.THREAT_HIGH, 4: Colors.THREAT_SEVERE,
                5: Colors.THREAT_CRITICAL}
LEVEL_ICONS = {1: "🟢", 2: "🟡", 3: "🟠", 4: "🔴", 5: "⛔"}
FAMILY_ICONS = {"raid": "🚨", "spam": "📢", "phishing": "🎣", "nuke": "💣", "integration": "🔌", "evasion": "🕵️",
                "model": "🔮"}
STATUS_ICONS = {"confirmed": "✅", "partial": "⚠️", "failed": "❌", "shadow": "👻", "refused": "⛔", "awaiting": "⏳",
                "attempted": "…", "denied": "✖️", "expired": "⌛", "skipped": "➖", "half": "⏳"}
FOOTER = "Signals are evidence, not proof. Staff decide."


def ts(value: Optional[float], style: str = "R") -> str:
    return f"<t:{int(value)}:{style}>" if value else "never"


def level_line(level: int) -> str:
    label, theme = LABELS[Level(level)]
    return f"{LEVEL_ICONS[level]} **{level} · {label}** ({theme})"


def _reasons(incident: dict, limit: int = 6) -> list[str]:
    out = []
    for s in reversed(incident.get("signals", [])):
        for r in s.get("reasons", []):
            text = explain(r["code"], **r.get("facts", {}))
            if text not in out:
                out.append(text)
            if len(out) >= limit:
                return out
    return out


def incident_embed(incident: dict, level: int, live: bool, rank: Optional[tuple[float, list]] = None) -> discord.Embed:
    family = incident.get("family", "?")
    severity = float(incident.get("severity", 0))
    color = (Colors.THREAT_CRITICAL if severity >= 0.9 else Colors.THREAT_SEVERE if severity >= 0.75
             else Colors.THREAT_HIGH if severity >= 0.5 else Colors.THREAT_ELEVATED)
    headline = {"raid": "Possible raid", "spam": "Spam", "phishing": "Phishing / malicious links",
                "nuke": "High-impact changes (anti-nuke)", "integration": "Webhook / bot activity",
                "evasion": "Possible ban evasion", "model": "Unusual activity pattern"}.get(family, family)
    status = "closed" if incident.get("status") == "closed" else "open"
    embed = discord.Embed(title=f"{FAMILY_ICONS.get(family, '🚨')} {headline} · incident {incident['id']}",
                          color=color,
                          description=f"Severity **{severity:.0%}** · threat level {level_line(level)} · {status}\n"
                                      f"Started {ts(incident.get('opened'))}, last activity {ts(incident.get('last'))}")
    observed = _reasons(incident)
    embed.add_field(name="Observed", value="\n".join(f"• {x}" for x in observed)[:1024] or "—", inline=False)
    embed.add_field(name="Scope", value=f"{len(incident.get('users', []))} account(s), "
                                         f"{len(incident.get('channels', []))} channel(s)", inline=True)
    counts = incident.get("counts", {})
    embed.add_field(name="Signals", value=", ".join(f"{k.replace('_', ' ')} ×{v}" for k, v in
                                                   sorted(counts.items(), key=lambda x: -x[1])[:5])[:1024] or "—",
                    inline=True)
    actions = incident.get("actions", [])[-8:]
    if actions:
        lines = [f"{STATUS_ICONS.get(a['status'], '•')} {a['kind'].replace('_', ' ')} ({a['authority']}): {a['status']}"
                 for a in actions]
        embed.add_field(name="Actions", value="\n".join(lines)[:1024], inline=False)
    snippets = incident.get("snippets", [])[:2]
    if snippets:
        embed.add_field(name="Evidence (kept 14 days)", value="\n".join(
            f"<#{s['channel']}>: ||{discord.utils.escape_markdown(s['text'])[:180]}||" for s in snippets)[:1024],
            inline=False)
    if rank is not None:
        score, why = rank
        embed.add_field(name="Priority", value=f"{score:.0%} likely a real attack"
                        + (f" (strongest evidence: {', '.join(n.replace('top_', '').replace('_', ' ') for n, _ in why)})"
                           if why else ""), inline=False)
    if not live:
        embed.add_field(name="Shadow mode", value="Automatic actions above were recorded, not carried out.",
                        inline=False)
    versions = incident.get("versions", {})
    embed.set_footer(text=f"{FOOTER} · {versions.get('rules', '')} · config v{versions.get('config', 0)}")
    return embed


def level_embed(old: int, new: int, reason: str, orders: dict, live: bool) -> discord.Embed:
    raised = new > old
    embed = discord.Embed(title=f"{'⬆️' if raised else '⬇️'} Threat level {old} → {new}", color=LEVEL_COLORS[new],
                          description=f"{level_line(new)}\n{reason}")
    standing = []
    if orders.get("slowmode_seconds"):
        standing.append(f"slowmode {orders['slowmode_seconds']}s on channels under a surge")
    if orders.get("probation_all_joins"):
        standing.append("every new join goes on probation")
    elif orders.get("probation_new_accounts"):
        standing.append("new accounts go on probation when they join")
    if orders.get("remove_new_member_links"):
        standing.append("links from members who joined today are removed")
    if orders.get("propose_pause_invites"):
        standing.append("staff are asked to confirm pausing invites")
    if orders.get("propose_lockdown"):
        standing.append(f"staff are asked to approve a lockdown ({orders['propose_lockdown']})")
    if orders.get("budget_factor", 1) < 1:
        standing.append(f"anti-nuke budgets ×{orders['budget_factor']}")
    embed.add_field(name="Standing orders", value="\n".join(f"• {x}" for x in standing) or "• normal operations",
                    inline=False)
    if not live:
        embed.set_footer(text="Shadow mode: standing orders are reported, not carried out.")
    return embed


def approval_embed(payload: dict, status: str = "pending", approvers: Optional[list] = None,
                   result: Optional[dict] = None) -> discord.Embed:
    plan = payload["plan"]
    kind = plan["kind"].replace("_", " ")
    two = payload.get("two_person")
    color = Colors.SECURITY if status in ("pending", "half") else Colors.SUCCESS if status == "approved" else Colors.UNAVAILABLE
    who = f"<@{payload['requested_by']}>" if payload.get("requested_by") else "Rudhabot (standing orders)"
    embed = discord.Embed(title=f"{'🔐' if two else '✋'} Proposed: {kind}", color=color,
                          description=f"{plan.get('reason', '')}\nRequested by {who} · needs "
                                      f"{Tier(plan['tier']).label}+"
                                      f"{' and two different staff' if two else ''} · expires {ts(payload.get('expires'))}")
    preview = payload.get("preview") or []
    if preview:
        embed.add_field(name="Preview", value="\n".join(f"• {x}" for x in preview[:12])[:1024], inline=False)
    if approvers:
        embed.add_field(name="Approved by", value=", ".join(f"<@{u}>" for u in approvers), inline=False)
    if status not in ("pending", "half"):
        line = f"{STATUS_ICONS.get(status, '•')} {status}"
        if result:
            line += f": {result.get('status')} · {result.get('detail', '')}"
        embed.add_field(name="Outcome", value=line[:1024], inline=False)
    if plan.get("incident_id"):
        embed.set_footer(text=f"incident {plan['incident_id']} · authorisation is re-checked when you press a button")
    else:
        embed.set_footer(text="Authorisation is re-checked when you press a button")
    return embed


CASE_ICONS = {"warn": "⚠️", "timeout": "⏱️", "untimeout": "▶️", "kick": "👢", "ban": "🔨", "unban": "🕊️",
              "purge": "🧹", "quarantine": "🧯", "release": "🔓", "probation": "🪖", "role_add": "🎖️",
              "role_remove": "🎖️", "role_move": "↕️", "role_perms": "🔑", "channel_perms": "🔐"}


def case_embed(case: dict) -> discord.Embed:
    extra = case.get("extra") or {}
    moderator = f"<@{case['moderator_id']}>" if case.get("moderator_id") else "Rudhabot (automatic)"
    subject = f"**Member:** <@{case['target_id']}> (`{case['target_id']}`)\n" if case.get("target_id") else ""
    if case["kind"].startswith("role_"):
        subject += f"**Role:** {extra.get('role_name', '?')} (`{extra.get('role', '?')}`)"
        if case["kind"] == "role_move":
            subject += f", position {extra['from']} → {extra['to']}\n"
        elif case["kind"] == "channel_perms":
            subject = (f"**Channel:** #{extra.get('channel_name')} · **For:** {extra.get('target_name')}\n"
                       f"**Permission:** {extra.get('permission')} → {extra.get('state')}\n")
        elif case["kind"] == "role_perms":
            subject += f"\n**Permission:** {extra.get('permission')} {'allowed' if extra.get('allow') else 'removed'}\n"
        else:
            subject += "\n"
    embed = discord.Embed(title=f"{CASE_ICONS.get(case['kind'], '📋')} Case #{case['case_no']} · "
                                f"{case['kind'].replace('_', ' ')}", color=Colors.MODLOG,
                          description=f"{subject}**Moderator:** {moderator}\n**Reason:** {case.get('reason') or '—'}")
    if case.get("duration_s"):
        from rudhabot.security.reasons import duration
        embed.add_field(name="Duration", value=duration(case["duration_s"]), inline=True)
    if case.get("incident_id"):
        embed.add_field(name="Incident", value=case["incident_id"], inline=True)
    if extra.get("removed") is not None:
        embed.add_field(name="Messages removed", value=str(extra["removed"]), inline=True)
    embed.timestamp = datetime.fromtimestamp(case["created"], timezone.utc)
    return embed


def status_embed(s: dict) -> discord.Embed:
    level = s.get("level", 1)
    embed = discord.Embed(title="🛡️ Security status", color=LEVEL_COLORS[level],
                          description=f"Readiness report for the garrison.\n{level_line(level)} "
                                      f"(since {ts(s.get('level_since'))}, {s.get('level_source')}"
                                      + (f", held until {ts(s['hold_until'])}" if s.get("hold_until") else "") + ")")
    embed.add_field(name="Mode", value=("**live**: standing orders are carried out" if s.get("mode") == "live" else
                                        "**shadow**: detect and report only (no automatic actions)"), inline=False)
    incidents = s.get("incidents", [])
    embed.add_field(name=f"Open incidents ({s.get('open_incidents', 0)})", value="\n".join(
        f"{FAMILY_ICONS.get(i['family'], '•')} `{i['id']}` {i['family']} · {i['severity']:.0%} · {i['users']} account(s)"
        for i in incidents) or "none", inline=False)
    locks = s.get("lockdowns", [])
    embed.add_field(name="Lockdowns", value="\n".join(f"#{l['id']} {l['scope']} · {l['channels']} channel(s) · "
                                                      f"lifts {ts(l['expires'])}" for l in locks) or "none", inline=True)
    embed.add_field(name="Restricted", value=f"{s.get('probation', 0)} on probation\n"
                                             f"{len(s.get('quarantined', []))} quarantined", inline=True)
    embed.add_field(name="Approvals waiting", value=str(s.get("pending_approvals", 0)), inline=True)
    acts = s.get("actions_24h", {})
    embed.add_field(name="Actions (24 h)", value=", ".join(f"{k} {v}" for k, v in sorted(acts.items())) or "none",
                    inline=False)
    models = s.get("models", {})
    if models.get("available"):
        embed.add_field(name="Clairvoyance", value=f"message model: {models.get('message')}\n"
                                                  f"incident model: {models.get('incident')}\n"
                                                  f"alert ranker: {models.get('ranker')}", inline=False)
    else:
        embed.add_field(name="Clairvoyance", value="models not loaded: rules only", inline=False)
    setup = []
    if not s.get("alert_channel"):
        setup.append("no alert channel (/security setup)")
    if not s.get("probation_role"):
        setup.append("no probation role (/security setup)")
    if setup:
        embed.add_field(name="Setup needed", value="\n".join(f"• {x}" for x in setup), inline=False)
    embed.set_footer(text=f"{s.get('rules')} · config v{s.get('config_version')} · last snapshot "
                          f"{'never' if not s.get('last_snapshot') else datetime.fromtimestamp(s['last_snapshot'], timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    return embed


SEVERITY_ICONS = {"critical": "⛔", "high": "🔴", "medium": "🟠", "low": "🟡", "info": "ℹ️"}


def posture_embed(score: int, findings: list) -> discord.Embed:
    color = Colors.THREAT_NORMAL if score >= 85 else Colors.THREAT_ELEVATED if score >= 65 else \
        Colors.THREAT_HIGH if score >= 45 else Colors.THREAT_SEVERE
    embed = discord.Embed(title=f"🔎 Security posture: {score}/100", color=color,
                          description="Inspection of permissions, safety settings and Rudhabot's own setup.")
    for f in findings[:10]:
        embed.add_field(name=f"{SEVERITY_ICONS[f.severity]} {f.title}", value=f"{f.detail}\n**Fix:** {f.fix}"[:1024],
                        inline=False)
    if len(findings) > 10:
        embed.set_footer(text=f"+{len(findings) - 10} more finding(s)")
    if not findings:
        embed.description += "\nNothing to report."
    return embed


def lockdown_embed(event: str, report: dict) -> discord.Embed:
    if event == "applied":
        embed = discord.Embed(title=f"🔒 Lockdown #{report['id']} in force", color=Colors.THREAT_SEVERE,
                              description=f"{report['changed']} permission change(s) across {report['channels']} "
                                          f"channel(s). Lifts {ts(report.get('expires'))}, or with "
                                          f"`/security lockdown lift`.")
    else:
        embed = discord.Embed(title=f"🔓 Lockdown #{report['id']} lifted ({report.get('reason')})",
                              color=Colors.THREAT_NORMAL, description=f"{report.get('restored', 0)} change(s) restored.")
    manual = report.get("manual") or []
    if manual:
        embed.add_field(name="Fix manually", value="\n".join(f"• {m}" for m in manual[:10])[:1024], inline=False)
    return embed


def digest_embed(incidents: list[dict]) -> discord.Embed:
    embed = discord.Embed(title="🗒️ Low-severity flags (last 10 minutes)", color=Colors.SECURITY,
                          description="Too weak to alert on their own; listed for review.")
    for inc in incidents[:10]:
        reasons = _reasons(inc, 2)
        priority = f" · priority {inc['_rank']:.0%}" if "_rank" in inc else ""
        embed.add_field(name=f"{FAMILY_ICONS.get(inc['family'], '•')} `{inc['id']}` {inc['family']} · "
                             f"{float(inc.get('severity', 0)):.0%}{priority}", value="; ".join(reasons)[:1024] or "—",
                        inline=False)
    return embed


def containment_embed(plan: dict, result: dict) -> discord.Embed:
    titles = {"quarantine": "🧯 Account quarantined", "kick_bot": "🔌 Bot removed", "delete_webhook": "🔌 Webhook removed",
              "revert_permissions": "↩️ Dangerous permissions undone"}
    embed = discord.Embed(title=titles.get(plan["kind"], plan["kind"]), color=Colors.THREAT_SEVERE,
                          description=f"{plan.get('reason', '')}\n**Result:** {result.get('status')} · "
                                      f"{result.get('detail', '')}")
    targets = plan.get("targets", [])
    if plan["kind"] == "quarantine" and targets:
        embed.add_field(name="Account", value=", ".join(f"<@{t}>" for t in targets[:5]), inline=False)
        embed.add_field(name="Next", value="Check the account's 2FA and sessions with its owner, review the audit log, "
                                           "then `/security quarantine release` (Admin).", inline=False)
    embed.set_footer(text="Anti-nuke containment is the one action Rudhabot takes on staff without asking.")
    return embed


def restore_embed(payload: dict) -> discord.Embed:
    result = payload["result"]
    embed = discord.Embed(title=f"♻️ Restore from snapshot #{payload['snapshot']}: {result['status']}",
                          color=Colors.SECURITY, description=result.get("detail", ""))
    if result.get("failed"):
        embed.add_field(name="Not done", value="\n".join(f"• {a}: {b}" for a, b in result["failed"][:8])[:1024],
                        inline=False)
    notes = result.get("data", {}).get("notes") or []
    if notes:
        embed.add_field(name="Notes", value="\n".join(f"• {n}" for n in notes[:6])[:1024], inline=False)
    return embed


def role_change_embed(p: dict) -> discord.Embed:
    if p["kind"] == "grant":
        text = (f"<@{p['actor']}> gave <@{p['target']}> **{p['role']}** through Rudhabot's role tools "
                f"(case #{p['case']}).")
    elif p["kind"] == "channel":
        text = (f"<@{p['actor']}> allowed {p['role']} a dangerous permission in #{p['channel']} through Rudhabot's "
                f"role tools (case #{p['case']}).")
    elif p["kind"] == "perms":
        text = (f"<@{p['actor']}> gave the role **{p['role']}** ({p.get('members', 0)} member(s)) a dangerous permission "
                f"through Rudhabot's role tools (case #{p['case']}).")
    else:
        text = (f"<@{p['actor']}> moved **{p['role']}** up from position {p['from']} to {p['to']} through Rudhabot's "
                f"role tools (case #{p['case']}).")
    embed = discord.Embed(title="🎖️ Dangerous role changed via Rudhabot", color=Colors.THREAT_ELEVATED,
                          description=text + f"\nPermissions: {', '.join(p.get('permissions', [])) or '—'}")
    embed.set_footer(text="Counted against the requester's anti-nuke budget. Undo with /role remove or /role move.")
    return embed


def simple(title: str, text: str, color: int = Colors.SECURITY) -> discord.Embed:
    return discord.Embed(title=title, description=text[:4000], color=color)

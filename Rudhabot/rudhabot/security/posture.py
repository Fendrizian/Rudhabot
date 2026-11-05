"""The permission and configuration audit (`/security tools action:health check`): a readiness inspection of the server.

Findings are graded critical / high / medium / low / info, each with the fix. The score starts at 100 and loses
points per finding (critical 25, high 12, medium 5, low 2). It checks Rudhabot's own access and position, @everyone
and broad roles with dangerous permissions, bots and integrations with dangerous permissions, Discord's own safety
settings for a server of this size, channel overwrites that hand out dangerous permissions, how many accounts hold
each dangerous permission, stale staff access, and whether native AutoMod and Rudhabot's own setup are in place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from rudhabot.security import perms as P
from rudhabot.security.model import GuildModel

WEIGHT = {"critical": 25, "high": 12, "medium": 5, "low": 2, "info": 0}
ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
#: What Rudhabot's security features actually need (it doesn't need Administrator).
NEEDED = {P.VIEW_AUDIT_LOG: "read the audit log (anti-nuke)", P.MANAGE_ROLES: "probation, quarantine, verification",
          P.MANAGE_CHANNELS: "slowmode, lockdowns, restores", P.MANAGE_MESSAGES: "delete spam, purge",
          P.MODERATE_MEMBERS: "timeouts", P.KICK_MEMBERS: "kick", P.BAN_MEMBERS: "ban",
          P.MANAGE_WEBHOOKS: "remove rogue webhooks", P.MANAGE_GUILD: "AutoMod rules, pausing invites, invite list",
          P.VIEW_CHANNEL: "see channels", P.SEND_MESSAGES: "post alerts", P.EMBED_LINKS: "alert embeds"}


@dataclass
class Finding:
    severity: str
    title: str
    detail: str
    fix: str

    def to_dict(self) -> dict:
        return {"severity": self.severity, "title": self.title, "detail": self.detail, "fix": self.fix}


def audit(guild: GuildModel, *, configured: dict, stale_staff: Iterable[int] = ()) -> tuple[int, list[Finding]]:
    f: list[Finding] = []
    add = lambda sev, title, detail, fix: f.append(Finding(sev, title, detail, fix))
    bot_perms = guild.base_permissions(guild.bot_id)
    bot_top = guild.bot_top_position

    # --------------------------------------------------------------- Rudhabot itself
    bot_role_perms = 0
    me = guild.member(guild.bot_id)
    if me:
        for role in guild.roles:
            if role.id in me.roles:
                bot_role_perms |= role.permissions
    if bot_role_perms & P.ADMINISTRATOR:
        add("low", "Rudhabot has Administrator",
            "It works, but Administrator is broader than the security features need; if Rudhabot's token were ever "
            "stolen, the thief would hold every permission.",
            "Optionally replace Administrator with: " + ", ".join(P.names(sum(NEEDED))) + ". Keep its role at the top.")
    missing = [why for bit, why in NEEDED.items() if not bot_perms & bit]
    if missing:
        add("high", "Rudhabot is missing permissions", "Without them it can't " + "; ".join(missing) + ".",
            "Grant them to Rudhabot's role.")
    above = [r for r in guild.roles if r.position > bot_top and not r.managed and r.id != guild.id]
    dangerous_above = [r for r in above if r.permissions & P.DANGEROUS]
    if dangerous_above:
        add("medium", "Roles above Rudhabot's role",
            f"{len(dangerous_above)} role(s) with dangerous permissions sit above Rudhabot "
            f"({', '.join(r.name for r in dangerous_above[:5])}); accounts holding them can't be quarantined.",
            "Move Rudhabot's role above every staff role (Server Settings > Roles), as intended.")

    # --------------------------------------------------------------- @everyone and broad roles
    everyone = guild.everyone.permissions
    if everyone & P.DANGEROUS:
        add("critical", "@everyone has dangerous permissions", ", ".join(P.names(everyone & P.DANGEROUS)),
            "Remove them from @everyone (Server Settings > Roles > @everyone).")
    for role in guild.roles:
        if role.id == guild.id or role.managed:
            continue
        danger = role.permissions & P.CRITICAL
        if danger and role.member_count >= 25:
            add("high", f"Broad role '{role.name}' has dangerous permissions",
                f"{role.member_count} members hold {', '.join(P.names(danger)[:4])}.",
                "Keep dangerous permissions on small, named staff roles only.")
        if role.permissions & P.ADMINISTRATOR and role.mentionable:
            add("low", f"Admin role '{role.name}' is mentionable", "Anyone can ping every admin at once.",
                "Turn off 'Allow anyone to @mention this role'.")
    admin_holders = sum(r.member_count for r in guild.roles if r.permissions & P.ADMINISTRATOR and not r.managed)
    if admin_holders > 6:
        add("medium", "Many administrators", f"About {admin_holders} accounts hold Administrator through roles.",
            "Every admin account is a way in for an attacker; give most staff narrower roles.")

    # --------------------------------------------------------------- bots and integrations
    for role in guild.roles:
        if role.managed and role.bot_id and role.bot_id != guild.bot_id and role.permissions & P.CRITICAL \
                and role.position < bot_top:                 # a bot placed above Rudhabot is the owner's choice
            add("medium", f"Bot role '{role.name}' has dangerous permissions",
                ", ".join(P.names(role.permissions & P.CRITICAL)[:5]),
                "A compromised bot can nuke the server: give each bot only what it needs.")

    # --------------------------------------------------------------- Discord's own safety settings
    big = guild.member_count >= 1000
    if guild.mfa_level < 1:
        add("high", "2FA isn't required for moderation",
            "Staff accounts without two-factor authentication can use moderation powers; stolen passwords are the "
            "most common way servers get nuked.",
            "Server Settings > Safety Setup > require 2FA for moderator actions (the owner needs 2FA on).")
    if guild.verification_level < (2 if big else 1):
        add("medium" if big else "low", "Low verification level",
            f"Level {guild.verification_level}; for {guild.member_count:,} members at least "
            f"{'Medium (registered for 5 minutes)' if big else 'Low (verified email)'} is recommended.",
            "Server Settings > Safety Setup > Verification Level.")
    if guild.explicit_content_filter < 2:
        add("low", "Explicit media filter isn't on for everyone", "Explicit images from members can get through.",
            "Server Settings > Safety Setup > filter media from all members.")
    if big and guild.default_notifications == 0:
        add("low", "Default notifications: all messages",
            "In a large server every new member gets pinged by every message, which makes spam raids louder.",
            "Server Settings > Overview > Default Notification Settings > Only @mentions.")
    if guild.automod_rules == 0:
        add("medium", "No native AutoMod rules",
            "Discord's AutoMod blocks spam and mention raids before any bot sees them.",
            "Run /security tools action:automod create to create Rudhabot's recommended rules.")

    # --------------------------------------------------------------- channel overwrites
    risky = []
    for channel in guild.channels:
        for ow in channel.overwrites:
            if ow.target_id == guild.id and ow.allow & (P.CRITICAL | P.MENTION_EVERYONE | P.MANAGE_MESSAGES):
                risky.append(f"#{channel.name}: {', '.join(P.names(ow.allow & (P.CRITICAL | P.MENTION_EVERYONE | P.MANAGE_MESSAGES))[:3])}")
    if risky:
        add("high", "Channels give @everyone dangerous permissions", "; ".join(risky[:6]) +
            (f" (+{len(risky) - 6} more)" if len(risky) > 6 else ""), "Remove those allows from the channel overwrites.")

    # --------------------------------------------------------------- staff
    stale = list(stale_staff)
    if stale:
        add("medium", "Stale staff access",
            f"{len(stale)} account(s) with moderation powers haven't been seen in 60+ days: "
            + ", ".join(f"<@{u}>" for u in stale[:8]),
            "Remove staff roles that are no longer used; dormant accounts are easy targets.")

    # --------------------------------------------------------------- Rudhabot's own setup
    if not configured.get("alert_channel"):
        add("high", "No security alert channel", "Alerts and approval requests have nowhere to go.",
            "/security setup alert_channel:#channel")
    if not configured.get("probation_role"):
        add("medium", "No probation role", "Risky joins can't be restricted automatically.",
            "Create a role that can't post links or attach files (or sees only a welcome channel), then "
            "/security setup probation_role:@role")
    if configured.get("mode") == "shadow":
        add("info", "Automation is in shadow mode", "Everything is detected and reported; nothing is done automatically.",
            "When you are happy with what it reports: /security setup mode:live")
    score = max(0, 100 - sum(WEIGHT[x.severity] for x in f))
    f.sort(key=lambda x: ORDER[x.severity])
    return score, f

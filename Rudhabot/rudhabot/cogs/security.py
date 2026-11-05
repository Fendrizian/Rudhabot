"""Server security: the event listeners, the alert/approval posts and the /security commands.

Kept deliberately small for staff; everything else (detection, anti-nuke, levels, approvals, models) runs by itself.
    everyday:  status · incident · lockdown · unlock · level · release · ask
    set-up:    setup · staff · settings · exempt · tools (health check, drills, snapshots, AutoMod, verification, models)

Listeners turn Discord events into security events (security/discord_port.py) and hand them to the service; nothing
here decides anything. Alerts go to the configured alert channel, cases to the moderation log.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import PICKER_PERMISSIONS
from rudhabot.security.clearance import TIER_LABELS, Refused, Tier, check
from rudhabot.security.policy import BY_KEY, LIST_KEYS, SETTINGS, SecurityConfig
from rudhabot.ui import security_embeds as E
from rudhabot.ui.branding import brand
from rudhabot.ui.theme import Colors
from rudhabot.views.security import DYNAMIC_ITEMS, approval_view, incident_view, verify_view

log = logging.getLogger("rudhabot.security")
REASON = app_commands.Range[str, 1, 300]
DRILL_COOLDOWN_S = 60


class SecurityCog(ServiceCog):
    security = app_commands.Group(name="security", description="Server security: status, emergencies, configuration",
                                  default_permissions=PICKER_PERMISSIONS["helper"],
                                  guild_only=True)

    def __init__(self, bot):
        super().__init__(bot)
        self.sec = self.services.security
        self.sec.notify = self.notify
        self.incident_posts: dict[str, tuple[int, int]] = {}
        self.approval_posts: dict[str, dict] = {}
        self._drills_running: set[int] = set()
        self._drill_last: dict[int, float] = {}
        self._warned: set[int] = set()

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(*DYNAMIC_ITEMS)
        self.ticker.start()

    async def cog_unload(self) -> None:
        self.ticker.cancel()
        await self.sec.shutdown()

    # ================================================================ listeners
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or (message.author.bot and message.webhook_id is None):
            return
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            return
        from rudhabot.security.discord_port import message_event
        try:
            event = message_event(message, lambda m: self.sec.tier_of(message.guild.id, m.id))
        except Exception:
            log.exception("could not read a message for security")
            return
        await self.sec.handle(message.guild.id, event)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        from rudhabot.security.discord_port import join_event
        await self.sec.handle(member.guild.id, join_event(member))

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        await self.sec.note_ban(guild.id, user.id, user.name, user.avatar.key if user.avatar else None)

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry) -> None:
        from rudhabot.security.discord_port import audit_event
        try:
            event = audit_event(entry, lambda m: self.sec.tier_of(entry.guild.id, m.id) if m else 0)
        except Exception:
            log.exception("could not read an audit-log entry for security")
            return
        await self.sec.handle(entry.guild.id, event)

    @tasks.loop(seconds=20)
    async def ticker(self) -> None:
        try:
            for guild in self.bot.guilds:
                self.sec.runtime(guild.id)
            await self.sec.tick()
        except Exception:
            log.exception("security tick failed")

    @ticker.before_loop
    async def _ready(self) -> None:
        await self.bot.wait_until_ready()
        if self.sec.models is not None and not self.sec.models.loaded:
            await asyncio.to_thread(self.sec.models.load)            # off the event loop
            log.info("security models: %s", {k: v for k, v in self.sec.models.status().items()
                                              if k in ("message", "incident", "ranker")})

    # ================================================================ posting
    def _channels(self, guild_id: int) -> tuple[int, int]:
        cfg = self.sec.config(guild_id)
        alert = int(cfg["alert_channel"] or 0)
        return alert, int(cfg["modlog_channel"] or alert)

    async def _post(self, channel_id: int, guild_id: int, **message) -> Optional[discord.Message]:
        if not channel_id:
            if guild_id not in self._warned:
                self._warned.add(guild_id)
                log.warning("security alerts for guild %s have nowhere to go: /security setup alert_channel", guild_id)
            return None
        return await self.services.messenger.send(channel_id, **message)

    async def notify(self, kind: str, guild_id: int, payload: dict) -> None:
        alert, modlog = self._channels(guild_id)
        m = self.services.messenger
        if kind == "incident":
            inc = payload["incident"]
            if not inc.get("snippets"):                    # saved to their own table once the incident is stored
                inc["snippets"] = self.sec.store.snippets(guild_id, inc["id"])
            # alerts are shown by severity: on the benchmark the ranker only beats severity on digest-level flags
            embed = E.incident_embed(inc, payload.get("level", 1), payload.get("live", False))
            refs = self._cross_refs(guild_id, inc)
            if refs:
                embed.add_field(name="Cross-references", value="\n".join(refs)[:1024], inline=False)
            ref = self.incident_posts.get(inc["id"])
            if payload.get("new") or ref is None:
                msg = await self._post(alert, guild_id, embed=embed, view=incident_view(inc["id"]))
                if msg is not None:
                    self.incident_posts[inc["id"]] = (msg.channel.id, msg.id)
            else:
                await m.edit(*ref, embed=embed)
        elif kind == "incident_closed":
            inc = payload["incident"]
            ref = self.incident_posts.pop(inc["id"], None)
            if ref:
                await m.edit(*ref, embed=E.incident_embed(inc, inc.get("peak_level", 1), True))
        elif kind == "level":
            await self._post(alert, guild_id, embed=E.level_embed(payload["old"], payload["new"], payload["reason"],
                                                                  payload["orders"], payload.get("live", False)))
        elif kind == "approval":
            msg = await self._post(alert, guild_id, embed=E.approval_embed(payload), view=approval_view(payload["token"]))
            self.approval_posts[payload["token"]] = payload
            if msg is not None:
                self.sec.store.set_approval_message(payload["token"], msg.channel.id, msg.id)
        elif kind == "approval_update":
            token = payload["token"]
            original = self.approval_posts.get(token) or self._rebuild_approval(token)
            approval = self.sec.store.approval(token)
            if original is None or approval is None or not approval.get("message"):
                return
            status = payload["status"]
            embed = E.approval_embed(original, status, payload.get("approvers"), payload.get("result"))
            await m.edit(*approval["message"], embed=embed, view=approval_view(token, open_=status == "half"))
            if status != "half":
                self.approval_posts.pop(token, None)
        elif kind == "case":
            await self._post(modlog, guild_id, embed=E.case_embed(payload["case"]))
        elif kind == "lockdown":
            await self._post(alert, guild_id, embed=E.lockdown_embed(payload["event"], payload))
        elif kind == "digest":
            await self._post(alert, guild_id, embed=E.digest_embed(self._ranked(guild_id, payload["incidents"])))
        elif kind == "containment":
            await self._post(alert, guild_id, embed=E.containment_embed(payload["plan"], payload["result"]))
        elif kind == "restore":
            await self._post(alert, guild_id, embed=E.restore_embed(payload))
        elif kind == "settings":
            await self._post(alert, guild_id, embed=E.settings_embed(payload))

    def _cross_refs(self, guild_id: int, inc: dict) -> list[str]:
        """What member standing and the anomaly monitor know about the (first few) members in an alert."""
        if len(inc.get("users") or []) > 10:            # a raid wave: no single member's history is the story
            return []
        try:
            return self.services.standing.cross_refs_for_incident(guild_id, inc)
        except Exception:
            log.exception("cross-references for incident %s failed", inc.get("id"))
            return []

    def _ranked(self, guild_id: int, incidents: list[dict]) -> list[dict]:
        """Most likely real first: the alert ranker when it is on and loaded, otherwise severity."""
        models = self.sec.models
        if models is not None and models.ranker is not None and self.sec.config(guild_id)["models.ranker"]:
            from rudhabot.ml.security.wargame import rank_vector
            from rudhabot.security.engine import Incident
            for inc in incidents:
                scored = models.rank(rank_vector(Incident.from_dict(inc)))
                inc["_rank"] = scored[0] if scored else float(inc.get("severity", 0))
            return sorted(incidents, key=lambda i: -i["_rank"])
        return sorted(incidents, key=lambda i: -float(i.get("severity", 0)))

    def _rebuild_approval(self, token: str) -> Optional[dict]:
        approval = self.sec.store.approval(token)
        if approval is None:
            return None
        action = self.sec.store.action(approval["action_id"])
        if action is None:
            return None
        return {"token": token, "plan": {"kind": action["kind"], "reason": action["reason"],
                                         "tier": approval["required_tier"], "incident_id": action["incident_id"]},
                "preview": [], "two_person": approval["two_person"], "requested_by": approval["requested_by"],
                "expires": approval["expires"]}

    # ================================================================ buttons
    async def _ephemeral(self, interaction: discord.Interaction, title: str, text: str, color: int = Colors.SECURITY):
        embed = E.simple(title, text, color)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_approval_button(self, interaction: discord.Interaction, op: str, token: str) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if op == "ok":
                message, _ = await self.sec.approve(interaction.guild.id, token, interaction.user.id)
            else:
                message = await self.sec.deny(interaction.guild.id, token, interaction.user.id)
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
        await self._ephemeral(interaction, "Done", message, Colors.SUCCESS)

    async def on_verify_button(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        text = await self.sec.verify(interaction.guild.id, interaction.user.id)
        await self._ephemeral(interaction, "Verification", text)

    async def on_verdict_button(self, interaction: discord.Interaction, incident_id: str, real: bool) -> None:
        if interaction.guild is None:
            return
        try:
            text = await self.sec.verdict(interaction.guild.id, incident_id, interaction.user.id, real)
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not recorded", str(refusal), Colors.UNAVAILABLE)
        await interaction.response.send_message(embed=brand(E.simple("Verdict recorded", text, Colors.SUCCESS),
                                                            "learning from staff verdicts"), ephemeral=True)

    async def on_report_button(self, interaction: discord.Interaction, incident_id: str) -> None:
        try:
            self._need(interaction, "view_incidents")
            text, source = await self.sec.incident_report(interaction.guild.id, incident_id, use_llm=False)
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not allowed", str(refusal), Colors.UNAVAILABLE)
        await self._ephemeral(interaction, f"📋 After-action report · {incident_id}", text)

    # ================================================================ helpers
    def _need(self, interaction: discord.Interaction, action: str) -> Tier:
        tier = self.sec.tier_of(interaction.guild.id, interaction.user.id)
        check(int(tier), action)
        return tier

    async def _guarded(self, interaction: discord.Interaction, action: str) -> bool:
        try:
            self._need(interaction, action)
            return True
        except Refused as refusal:
            await self._ephemeral(interaction, "⛔ Not allowed", str(refusal), Colors.UNAVAILABLE)
            return False

    def _save(self, interaction, config: SecurityConfig, note: str) -> SecurityConfig:
        return self.sec.save_config(interaction.guild.id, config, interaction.user.id, note)

    # ================================================================ /security
    # ================================================================ everyday commands (staff)
    @security.command(name="status", description="Threat level, open incidents, lockdowns and who is restricted")
    async def status(self, interaction: discord.Interaction):
        if not await self._guarded(interaction, "view_incidents"):
            return
        embed = E.status_embed(self.sec.status(interaction.guild.id))
        recent = [i for i in self.sec.store.incidents(interaction.guild.id, "closed", 5)]
        if recent:
            embed.add_field(name="Recently closed", value="\n".join(
                f"{E.FAMILY_ICONS.get(i['family'], '•')} `{i['id']}` {i['family']} · {E.ts(i.get('last'))}"
                for i in recent), inline=False)
        embed.add_field(name="Next", value="`/security incident <id>` for details · `/security lockdown` in an "
                        "emergency · `/security release` to free someone", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @security.command(name="incident", description="One incident: details, its after-action report, or remove evidence")
    @app_commands.describe(action="details (default), report, or redact (delete the kept message snippets)")
    async def incident(self, interaction: discord.Interaction, incident_id: str,
                       action: Literal["details", "report", "redact"] = "details"):
        if not await self._guarded(interaction, "incident_manage" if action == "redact" else "view_incidents"):
            return
        gid = interaction.guild.id
        rt = self.sec.runtime(gid)
        if action == "redact":
            removed = self.sec.store.redact_snippets(gid, incident_id)
            inc = rt.engine.incidents.get(incident_id)
            if inc is not None:
                inc.snippets = []
            return await self._ephemeral(interaction, "Evidence removed", f"{removed} snippet(s) deleted.")
        if action == "report":
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                text, source = await self.sec.incident_report(gid, incident_id, user_id=interaction.user.id)
            except Refused as refusal:
                return await self._ephemeral(interaction, "No such incident", str(refusal), Colors.FAIL)
            return await self._ephemeral(interaction, f"📋 Incident {incident_id}"
                                         + (" · AI draft: check it against the facts" if source == "llm" else ""), text)
        inc = rt.engine.incidents.get(incident_id)
        data = inc.to_dict() if inc else self.sec.store.incident(gid, incident_id)
        if data is None:
            return await self._ephemeral(interaction, "No such incident", f"`{incident_id}` (see /security status)",
                                         Colors.FAIL)
        data["snippets"] = self.sec.store.snippets(gid, incident_id)
        await interaction.response.send_message(embed=E.incident_embed(data, int(rt.engine.level), rt.config.live),
                                                view=incident_view(incident_id), ephemeral=True)

    @security.command(name="lockdown", description="Lock the server, a channel or a category (preview, then approval)")
    @app_commands.describe(target="A channel or category; leave empty for the whole server",
                           minutes="Lifts itself after this long (default 30)")
    async def lockdown(self, interaction: discord.Interaction, reason: REASON,
                       target: Optional[discord.abc.GuildChannel] = None,
                       minutes: app_commands.Range[int, 5, 1440] = 30):
        scope = "server" if target is None else "channels"
        try:
            token, preview = await self.sec.request_lockdown(interaction.guild.id, interaction.user.id, scope,
                                                             [target.id] if target else [], minutes, reason)
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
        two = self.sec.config(interaction.guild.id)["approvals.two_person"]
        await self._ephemeral(interaction, "🔐 Lockdown requested",
                              ("Another Senior Moderator+ approves it in the alert channel." if two else
                               "Confirm it in the alert channel.") + "\n\n**Preview**\n"
                              + "\n".join(f"• {x}" for x in preview[:12]))

    @security.command(name="unlock", description="End a lockdown now and put the permissions back")
    async def unlock(self, interaction: discord.Interaction, lockdown_id: Optional[int] = None):
        active = self.sec.store.active_lockdowns(interaction.guild.id)
        if lockdown_id is None:
            if not active:
                return await self._ephemeral(interaction, "No lockdown", "Nothing is locked down.")
            lockdown_id = active[-1]["id"]
        await interaction.response.defer(thinking=True)
        try:
            report = await self.sec.lift_lockdown(interaction.guild.id, lockdown_id, interaction.user.id, "lifted by staff")
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
        await interaction.followup.send(embed=E.lockdown_embed("lifted", report))

    @security.command(name="level", description="Set the threat level by hand (Senior Moderator)")
    @app_commands.describe(hold_minutes="Keep it at least this long (0 = let it step down normally)")
    async def level(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 5],
                    reason: REASON = "set by staff", hold_minutes: app_commands.Range[int, 0, 1440] = 60):
        try:
            await self.sec.set_level(interaction.guild.id, interaction.user.id, level, reason, hold_minutes)
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not allowed", str(refusal), Colors.UNAVAILABLE)
        await interaction.response.send_message(embed=E.simple(
            f"Threat level set to {level}", f"{E.level_line(level)}\n{reason}", E.LEVEL_COLORS[level]))

    @security.command(name="release", description="Free a member from probation or quarantine")
    async def release(self, interaction: discord.Interaction, member: discord.Member):
        gid = interaction.guild.id
        await interaction.response.defer(ephemeral=True, thinking=True)     # restoring many roles takes a while
        done = []
        try:
            if self.sec.store.quarantined(gid, member.id):
                done.append(await self.sec.release_quarantine(gid, interaction.user.id, member.id))
            if any(p["user_id"] == member.id for p in self.sec.store.probation(gid)):
                done.append(await self.sec.release_probation(gid, interaction.user.id, member.id))
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
        if not done:
            return await self._ephemeral(interaction, "Nothing to release",
                                         f"{member.mention} isn't on probation or quarantined.")
        await self._ephemeral(interaction, "🔓 Released", "\n".join(done), Colors.SUCCESS)

    @security.command(name="ask", description="Ask about the server's security state in plain words (read-only)")
    async def ask(self, interaction: discord.Interaction, question: app_commands.Range[str, 3, 400]):
        if not await self._guarded(interaction, "ask"):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        text, source = await self.sec.ask(interaction.guild.id, question, interaction.user.id)
        await self._ephemeral(interaction, "Security Q&A" + (" (AI, read-only)" if source == "llm" else ""), text)

    # ================================================================ set-up (Admins)
    @security.command(name="setup", description="Channels, roles and mode security uses; empty shows the current setup")
    @app_commands.describe(mode="shadow: detect and report only · live: carry out the automatic responses")
    async def setup(self, interaction: discord.Interaction, alert_channel: Optional[discord.TextChannel] = None,
                    modlog_channel: Optional[discord.TextChannel] = None, probation_role: Optional[discord.Role] = None,
                    verified_role: Optional[discord.Role] = None, quarantine_role: Optional[discord.Role] = None,
                    mode: Optional[Literal["shadow", "live"]] = None):
        cfg = self.sec.config(interaction.guild.id)
        options = (("alert_channel", alert_channel), ("modlog_channel", modlog_channel),
                   ("probation_role", probation_role), ("verified_role", verified_role),
                   ("quarantine_role", quarantine_role))
        if all(v is None for _, v in options) and mode is None:
            if not await self._guarded(interaction, "posture"):
                return
            lines = [f"**{k.replace('_', ' ')}:** " + (f"<#{cfg[k]}>" if "channel" in k else f"<@&{cfg[k]}>")
                     if cfg[k] else f"**{k.replace('_', ' ')}:** not set" for k, _ in options]
            lines.append(f"**mode:** {cfg['mode']}")
            return await self._ephemeral(interaction, "Security setup", "\n".join(lines)
                                         + "\n\nChange any of these with the options of this command.")
        if not await self._guarded(interaction, "config"):
            return
        changes = []
        for key, value in options:
            if value is not None:
                me = getattr(interaction.guild, "me", None)
                if isinstance(value, discord.Role) and me is not None and value >= me.top_role:
                    return await self._ephemeral(interaction, "⛔ Role too high",
                                                 f"{value.mention} is above Rudhabot's role, so it can't be managed.",
                                                 Colors.UNAVAILABLE)
                cfg = cfg.with_value(key, value.id)
                changes.append(f"{key.replace('_', ' ')}: {value.mention}")
        if mode is not None:
            cfg = cfg.with_value("mode", mode)
            changes.append(f"mode: **{mode}**" + (" (automatic responses on)" if mode == "live" else
                                                  " (detect and report only)"))
        cfg = self._save(interaction, cfg, "setup: " + ", ".join(c.split(":")[0] for c in changes))
        await self._ephemeral(interaction, f"Setup saved (v{cfg.version})", "\n".join(changes))

    @security.command(name="staff", description="Which staff tier a role has; give a role and tier to change it")
    @app_commands.describe(tier="1 Helper, 2 Moderator, 3 Senior Moderator, 4 Admin; 0 removes the role's tier")
    async def staff(self, interaction: discord.Interaction, role: Optional[discord.Role] = None,
                    tier: Optional[app_commands.Range[int, 0, 4]] = None, member: Optional[discord.Member] = None):
        gid = interaction.guild.id
        if member is not None:
            t = self.sec.tier_of(gid, member.id)
            return await self._ephemeral(interaction, "Staff tier", f"{member.mention}: **{t.label}** ({t.rank})")
        if role is not None and tier is not None:
            try:
                cfg = self.sec.set_staff_tier(gid, interaction.user.id, role.id, tier)
            except Refused as refusal:
                return await self._ephemeral(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
            label = "no tier" if not tier else f"{Tier(tier).label} ({Tier(tier).rank})"
            return await self._ephemeral(interaction, f"Saved (v{cfg.version})", f"{role.mention}: {label}")
        cfg = self.sec.config(gid)
        lines = [f"<@&{rid}>: {TIER_LABELS[Tier(t)]}" for rid, t in sorted(cfg["tiers"].items(), key=lambda x: -x[1])]
        extra = ("Discord permissions count too: Administrator/Manage Server = Admin, Ban Members = Senior Moderator, "
                 "Kick/Timeout Members = Moderator, Manage Messages = Helper." if cfg["derive_tiers"] else "")
        await self._ephemeral(interaction, "Staff tiers", "\n".join(lines + ["", extra]) if lines
                              else f"No roles mapped yet. {extra}")

    async def _setting_names(self, interaction, current: str) -> list[app_commands.Choice[str]]:
        keys = [s.key for s in SETTINGS] + list(LIST_KEYS) + ["rollback"]
        return [app_commands.Choice(name=k, value=k) for k in keys if current.lower() in k][:25]

    @security.command(name="settings", description="Detection settings: list, explain or change one (Admin to change)")
    @app_commands.describe(key="A setting (empty lists them all)",
                           value="New value. Lists take +item / -item; key 'rollback' takes a version number")
    async def settings(self, interaction: discord.Interaction, key: Optional[str] = None,
                       value: Optional[app_commands.Range[str, 1, 100]] = None):
        gid = interaction.guild.id
        cfg = self.sec.config(gid)
        if value is None:
            if not await self._guarded(interaction, "posture"):
                return
            if key in BY_KEY:
                s = BY_KEY[key]
                return await self._ephemeral(interaction, key, f"**{cfg[key]}** (default {s.default})\n{s.help}")
            if key in LIST_KEYS:
                items = [f"<#{x}>" if key.startswith("channels") else f"`{x}`" for x in cfg[key]]
                return await self._ephemeral(interaction, key, ", ".join(items) or "empty")
            if key == "rollback":
                rows = self.sec.store.config_history(gid)
                return await self._ephemeral(interaction, "Versions", "\n".join(
                    f"v{r['version']} · {E.ts(r['created'])} · <@{r['by']}> · {r['note']}" for r in rows)
                    or "Defaults only.")
            changed = [f"`{s.key}` = **{cfg[s.key]}**" for s in SETTINGS if cfg[s.key] != s.default]
            lists = [f"`{k}`: {len(cfg[k])}" for k in LIST_KEYS if cfg[k]]
            exempt = self.sec.store.allows(gid)
            text = ("Everything else is at its recommended default. Pick a `key` to see what it does.\n\n"
                    "**Changed:** " + (", ".join(changed) or "nothing") + "\n**Lists:** " + (", ".join(lists) or "empty")
                    + f"\n**Exemptions:** {len(exempt)} active (`/security exempt`)")
            return await self._ephemeral(interaction, f"Security settings (v{cfg.version})", text)
        if not await self._guarded(interaction, "config"):
            return
        try:
            if key == "rollback":
                values = self.sec.store.config_version(gid, int(value))
                if values is None:
                    raise ValueError(f"there is no version {value}")
                new, note = SecurityConfig(values), f"rollback to v{value}"
            elif key in LIST_KEYS:
                add = not value.startswith("-")
                item = value.lstrip("+-").strip().strip("<#>")
                new, note = cfg.with_list(key, item if not key.startswith("links") else item.lower(), add), f"{key} {value}"
            elif key in BY_KEY:
                new, note = cfg.with_value(key, value), f"{key}={value}"
            else:
                raise ValueError(f"unknown setting {key!r}")
        except (ValueError, TypeError) as error:
            return await self._ephemeral(interaction, "Not changed", str(error), Colors.FAIL)
        new = self._save(interaction, new, note)
        await self._ephemeral(interaction, f"Saved (v{new.version})", note)

    settings.autocomplete("key")(_setting_names)

    @security.command(name="exempt", description="Exempt a member, role, channel or domain for a while (empty lists them)")
    @app_commands.describe(value="A mention, an id or a domain", days="How long (0 removes the exemption)",
                           checks="Which checks to skip")
    async def exempt(self, interaction: discord.Interaction,
                     kind: Optional[Literal["member", "role", "channel", "domain"]] = None, value: Optional[str] = None,
                     days: app_commands.Range[int, 0, 90] = 7,
                     checks: Literal["all", "messages", "joins", "antinuke"] = "all"):
        gid = interaction.guild.id
        if kind is None or value is None:
            if not await self._guarded(interaction, "posture"):
                return
            rows = self.sec.store.allows(gid)
            return await self._ephemeral(interaction, "Exemptions", "\n".join(
                f"{a['kind']} `{a['value']}` · {', '.join(a['detectors'])} · until {E.ts(a['expires'])}" for a in rows)
                or "None.")
        store_kind = "user" if kind == "member" else kind
        value = value.strip().strip("<@&#!>").lower()
        if store_kind != "domain" and not value.isdigit():
            return await self._ephemeral(interaction, "Not an id", "Give a mention or a numeric id.", Colors.FAIL)
        if days == 0:
            if not await self._guarded(interaction, "allowlist"):
                return
            removed = [a for a in self.sec.store.allows(gid) if a["kind"] == store_kind and a["value"] == value]
            for a in removed:
                self.sec.store.remove_allow(gid, a["id"])
            self.sec.runtime(gid).allows_at = 0
            return await self._ephemeral(interaction, "Exemption removed" if removed else "Nothing to remove",
                                         f"{kind} `{value}`")
        try:
            text = await self.sec.add_exemption(gid, interaction.user.id, store_kind, value, checks, days)
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
        await self._ephemeral(interaction, "Exempted", text)

    @security.command(name="tools", description="Health check, drills, snapshots, restore, AutoMod, verification, models")
    @app_commands.describe(action="What to do", scenario="For a drill: which attack to rehearse",
                           channel="For the verification panel: where to post it",
                           snapshot_id="For a restore: which snapshot (default: the latest)")
    async def tools(self, interaction: discord.Interaction,
                    action: Literal["health check", "drill", "take snapshot", "restore snapshot", "automod preview",
                                    "automod create", "verification panel", "models"],
                    scenario: Optional[Literal["join_raid", "spam_raid", "phishing", "mention_bomb", "staff_nuke",
                                               "malicious_bot", "webhook_spam", "slow_drip"]] = None,
                    channel: Optional[discord.TextChannel] = None, snapshot_id: Optional[int] = None):
        gid = interaction.guild.id
        if action == "health check":
            if not await self._guarded(interaction, "posture"):
                return
            score, findings = self.sec.posture(gid)
            return await interaction.response.send_message(embed=E.posture_embed(score, findings), ephemeral=True)
        if action == "drill":
            if not await self._guarded(interaction, "drill"):
                return
            last = self._drill_last.get(interaction.user.id, 0.0)
            if gid in self._drills_running or time.monotonic() - last < DRILL_COOLDOWN_S:
                return await self._ephemeral(interaction, "One drill at a time",
                                             "A drill is running, or you ran one less than a minute ago.")
            self._drills_running.add(gid)
            self._drill_last[interaction.user.id] = time.monotonic()
            try:
                await interaction.response.defer(ephemeral=True, thinking=True)
                from rudhabot.security.drill import run_drill
                scenario = scenario or "join_raid"
                models = self.sec.models if self.sec.models is not None and self.sec.models.loaded else None
                report = await asyncio.to_thread(run_drill, scenario, self.sec.config(gid), None, models)
            finally:
                self._drills_running.discard(gid)
            embed = E.simple(f"🎯 Drill: {scenario.replace('_', ' ')}", report, Colors.SECURITY)
            if models is not None:
                brand(embed, "models included in the drill")
            return await interaction.followup.send(embed=embed, ephemeral=True)
        if action == "take snapshot":
            if not await self._guarded(interaction, "snapshot"):
                return
            snap_id = self.sec.take_snapshot(gid, f"manual by {interaction.user.id}")
            recent = self.sec.store.snapshots(gid, 5)
            return await self._ephemeral(interaction, f"📸 Snapshot #{snap_id} taken", "Recent: " + ", ".join(
                f"#{r['id']} {E.ts(r['created'])}" for r in recent))
        if action == "restore snapshot":
            try:
                token, preview = await self.sec.request_restore(gid, interaction.user.id, snapshot_id)
            except Refused as refusal:
                return await self._ephemeral(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
            return await self._ephemeral(interaction, "♻️ Restore requested", "Approve it in the alert channel "
                                         "(Admin).\n\n" + "\n".join(f"• {x}" for x in preview[:15]))
        if action.startswith("automod"):
            if not await self._guarded(interaction, "automod_sync"):
                return
            rules = automod_plan(self.sec.config(gid))
            if action == "automod preview":
                return await self._ephemeral(interaction, "AutoMod rules (preview)", "\n".join(
                    f"• **{n}**: {w}" for n, w, _ in rules) + "\n\nUse `automod create` to add them.")
            await interaction.response.defer(ephemeral=True, thinking=True)
            created, failed = await create_automod_rules(interaction.guild, rules, self.sec.config(gid)["alert_channel"])
            return await self._ephemeral(interaction, "AutoMod", f"Created: {', '.join(created) or 'none (already there)'}"
                                         + (f"\nNot created: {'; '.join(failed)}" if failed else ""))
        if action == "verification panel":
            if not await self._guarded(interaction, "verify_panel"):
                return
            if not self.sec.config(gid)["verified_role"]:
                return await self._ephemeral(interaction, "Set a verified role first", "/security setup verified_role:")
            if channel is None:
                return await self._ephemeral(interaction, "Pick a channel", "Choose where to post the panel.")
            await channel.send(embed=E.simple("✅ Verification", "Press the button to confirm you're a person and "
                                              "unlock the server.", Colors.SECURITY), view=verify_view())
            return await self._ephemeral(interaction, "Posted", f"Verification panel posted in {channel.mention}.")
        if not await self._guarded(interaction, "posture"):
            return
        from rudhabot.ui.clairvoyance_embeds import status_embed
        await interaction.response.send_message(embed=status_embed(self.services, gid), ephemeral=True)


# ---------------------------------------------------------------------------------------------- AutoMod
def automod_plan(cfg: SecurityConfig) -> list[tuple[str, str, dict]]:
    rules = [("Rudhabot: mention spam", f"block messages mentioning more than {cfg['messages.mention_limit'] + 2} "
              "members or roles, with raid protection", {"type": "mention_spam",
                                                          "limit": int(cfg["messages.mention_limit"]) + 2}),
             ("Rudhabot: spam", "Discord's own spam-content filter", {"type": "spam"})]
    deny = cfg["links.deny"]
    if deny:
        rules.append(("Rudhabot: denied links", f"block messages containing {len(deny)} denied domain(s)",
                      {"type": "keyword", "keywords": [f"*{d}*" for d in deny[:1000]]}))
    return rules


async def create_automod_rules(guild: discord.Guild, rules, alert_channel: int) -> tuple[list[str], list[str]]:
    created, failed = [], []
    try:
        existing = {r.name for r in await guild.fetch_automod_rules()}
    except discord.HTTPException as error:
        return [], [f"can't read AutoMod rules ({error.status})"]
    for name, _, spec in rules:
        if name in existing:
            continue
        actions = [discord.AutoModRuleAction(custom_message="Blocked by Rudhabot's AutoMod rules.")]
        if alert_channel:
            actions.append(discord.AutoModRuleAction(channel_id=alert_channel))
        if spec["type"] == "mention_spam":
            trigger = discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.mention_spam,
                                             mention_limit=spec["limit"], mention_raid_protection=True)
        elif spec["type"] == "spam":
            trigger = discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.spam)
        else:
            trigger = discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.keyword, keyword_filter=spec["keywords"])
        try:
            await guild.create_automod_rule(name=name, event_type=discord.AutoModRuleEventType.message_send,
                                            trigger=trigger, actions=actions, enabled=True,
                                            reason="Rudhabot recommended rules")
            created.append(name)
        except discord.HTTPException as error:
            failed.append(f"{name} ({error.status})")
    return created, failed

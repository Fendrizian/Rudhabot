"""Server security: the event listeners, the alert/approval posts and the /security commands.

Listeners turn Discord events into security events (security/discord_port.py) and hand them to the service; nothing
here decides anything. Alerts go to the configured alert channel, cases to the moderation log.

/security status | posture | mode | setup | level | incidents | incident view|report|redact | lockdown start|lift
/security snapshot take|list|restore | config view|set|history|rollback|list | clearance set|view|check
/security allow add|remove|list | quarantine list|release | probation list|release | verify-panel | automod
/security drill | ask | models
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from rudhabot.cogs.base import ServiceCog
from rudhabot.security.clearance import TIER_LABELS, Refused, Tier, check
from rudhabot.security.policy import BY_KEY, SETTINGS, SecurityConfig
from rudhabot.ui import security_embeds as E
from rudhabot.ui.theme import Colors
from rudhabot.views.security import DYNAMIC_ITEMS, approval_view, incident_view, verify_view

log = logging.getLogger("rudhabot.security")
REASON = app_commands.Range[str, 1, 300]


class SecurityCog(ServiceCog):
    security = app_commands.Group(name="security", description="Server security: status, emergencies, configuration",
                                  guild_only=True)
    incident_group = app_commands.Group(name="incident", description="One incident", parent=security)
    lockdown_group = app_commands.Group(name="lockdown", description="Scoped, time-limited lockdowns", parent=security)
    snapshot_group = app_commands.Group(name="snapshot", description="Server snapshots and restore", parent=security)
    config_group = app_commands.Group(name="config", description="Security settings (versioned)", parent=security)
    clearance_group = app_commands.Group(name="clearance", description="Staff clearance tiers", parent=security)
    allow_group = app_commands.Group(name="allow", description="Time-limited exceptions", parent=security)
    quarantine_group = app_commands.Group(name="quarantine", description="Quarantined accounts", parent=security)
    probation_group = app_commands.Group(name="probation", description="Members on probation", parent=security)

    def __init__(self, bot):
        super().__init__(bot)
        self.sec = self.services.security
        self.sec.notify = self.notify
        self.incident_posts: dict[str, tuple[int, int]] = {}
        self.approval_posts: dict[str, dict] = {}
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
        elif kind == "role_change":
            await self._post(alert, guild_id, embed=E.role_change_embed(payload))

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
    @security.command(name="status", description="Readiness report: threat level, incidents, lockdowns, models")
    async def status(self, interaction: discord.Interaction):
        if not await self._guarded(interaction, "view_incidents"):
            return
        await interaction.response.send_message(embed=E.status_embed(self.sec.status(interaction.guild.id)),
                                                ephemeral=True)

    @security.command(name="posture", description="Inspect permissions and safety settings (with fixes)")
    async def posture(self, interaction: discord.Interaction):
        if not await self._guarded(interaction, "posture"):
            return
        score, findings = self.sec.posture(interaction.guild.id)
        await interaction.response.send_message(embed=E.posture_embed(score, findings), ephemeral=True)

    @security.command(name="mode", description="shadow: detect and report only · live: carry out standing orders (Admin)")
    async def mode(self, interaction: discord.Interaction, mode: Literal["shadow", "live"]):
        if not await self._guarded(interaction, "mode"):
            return
        cfg = self._save(interaction, self.sec.config(interaction.guild.id).with_value("mode", mode), f"mode {mode}")
        text = ("Live: Rudhabot now carries out the standing orders (deleting spam, slowmode, probation, short "
                "timeouts, anti-nuke quarantine). Everything else still needs staff." if mode == "live" else
                "Shadow: everything is detected and reported; no automatic action is taken.")
        await self._ephemeral(interaction, f"Mode: {mode} (config v{cfg.version})", text)

    @security.command(name="setup", description="Set the alert channel, mod log and roles security uses (Admin)")
    async def setup(self, interaction: discord.Interaction, alert_channel: Optional[discord.TextChannel] = None,
                    modlog_channel: Optional[discord.TextChannel] = None, probation_role: Optional[discord.Role] = None,
                    verified_role: Optional[discord.Role] = None, quarantine_role: Optional[discord.Role] = None):
        if not await self._guarded(interaction, "config"):
            return
        cfg = self.sec.config(interaction.guild.id)
        changes = []
        for key, value in (("alert_channel", alert_channel), ("modlog_channel", modlog_channel),
                           ("probation_role", probation_role), ("verified_role", verified_role),
                           ("quarantine_role", quarantine_role)):
            if value is not None:
                me = getattr(interaction.guild, "me", None)
                if isinstance(value, discord.Role) and me is not None and value >= me.top_role:
                    return await self._ephemeral(interaction, "⛔ Role too high",
                                                 f"{value.mention} is above Rudhabot's role, so it can't be managed.",
                                                 Colors.UNAVAILABLE)
                cfg = cfg.with_value(key, value.id)
                changes.append(f"{key.replace('_', ' ')}: {value.mention}")
        if not changes:
            return await self._ephemeral(interaction, "Nothing changed", "Pick at least one option.")
        cfg = self._save(interaction, cfg, "setup: " + ", ".join(c.split(":")[0] for c in changes))
        await self._ephemeral(interaction, f"Setup saved (config v{cfg.version})", "\n".join(changes))

    @security.command(name="level", description="Set the threat level by hand, held for a while (Senior Moderator)")
    @app_commands.describe(hold_minutes="Keep this level at least this long (0 = let it decay normally)")
    async def level(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 5], reason: REASON,
                    hold_minutes: app_commands.Range[int, 0, 1440] = 60):
        try:
            await self.sec.set_level(interaction.guild.id, interaction.user.id, level, reason, hold_minutes)
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not allowed", str(refusal), Colors.UNAVAILABLE)
        await interaction.response.send_message(embed=E.simple(
            f"Threat level set to {level}", f"{E.level_line(level)}\n{reason}", E.LEVEL_COLORS[level]))

    @security.command(name="incidents", description="Recent incidents")
    async def incidents(self, interaction: discord.Interaction, status: Optional[Literal["open", "closed"]] = None):
        if not await self._guarded(interaction, "view_incidents"):
            return
        rows = self.sec.store.incidents(interaction.guild.id, status, 15)
        open_now = {i.id: i for i in (self.sec.runtime(interaction.guild.id).engine.incidents.values())}
        lines = []
        for inc in rows:
            live = open_now.get(inc["id"])
            sev = live.severity if live else inc.get("severity", 0)
            lines.append(f"{E.FAMILY_ICONS.get(inc['family'], '•')} `{inc['id']}` {inc['family']} · {sev:.0%} · "
                         f"{inc.get('status')} · {E.ts(inc.get('last'))}")
        await self._ephemeral(interaction, "Incidents", "\n".join(lines) or "None recorded.")

    @incident_group.command(name="view", description="One incident in full")
    async def incident_view(self, interaction: discord.Interaction, incident_id: str):
        if not await self._guarded(interaction, "view_incidents"):
            return
        rt = self.sec.runtime(interaction.guild.id)
        inc = rt.engine.incidents.get(incident_id)
        data = inc.to_dict() if inc else self.sec.store.incident(interaction.guild.id, incident_id)
        if data is None:
            return await self._ephemeral(interaction, "No such incident", incident_id, Colors.FAIL)
        if inc is not None:
            data["snippets"] = self.sec.store.snippets(interaction.guild.id, incident_id)
        await interaction.response.send_message(embed=E.incident_embed(data, int(rt.engine.level), rt.config.live),
                                                ephemeral=True)

    @incident_group.command(name="report", description="After-action report (an LLM draft when configured)")
    async def incident_report(self, interaction: discord.Interaction, incident_id: str):
        if not await self._guarded(interaction, "view_incidents"):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            text, source = await self.sec.incident_report(interaction.guild.id, incident_id)
        except Refused as refusal:
            return await self._ephemeral(interaction, "No such incident", str(refusal), Colors.FAIL)
        title = f"📋 Incident {incident_id}" + (" · AI draft: check it against the facts" if source == "llm" else "")
        await self._ephemeral(interaction, title, text)

    @incident_group.command(name="redact", description="Delete the message snippets kept as evidence (Moderator)")
    async def incident_redact(self, interaction: discord.Interaction, incident_id: str):
        if not await self._guarded(interaction, "incident_manage"):
            return
        removed = self.sec.store.redact_snippets(interaction.guild.id, incident_id)
        inc = self.sec.runtime(interaction.guild.id).engine.incidents.get(incident_id)
        if inc is not None:
            inc.snippets = []
        await self._ephemeral(interaction, "Evidence removed", f"{removed} snippet(s) deleted from incident {incident_id}.")

    # ------------------------------------------------------------------ lockdowns
    @lockdown_group.command(name="start", description="Request a lockdown: preview first, then approval (Moderator+)")
    @app_commands.describe(scope="Whole server, one channel, or a whole category",
                           target="The channel or category (for channel/category scope)")
    async def lockdown_start(self, interaction: discord.Interaction, scope: Literal["server", "channel", "category"],
                             reason: REASON, target: Optional[discord.abc.GuildChannel] = None,
                             minutes: app_commands.Range[int, 5, 1440] = 30):
        channels = [target.id] if target is not None else []
        if scope != "server" and target is None:
            return await self._ephemeral(interaction, "Pick a target", "Choose the channel or category to lock.")
        try:
            token, preview = await self.sec.request_lockdown(interaction.guild.id, interaction.user.id,
                                                             "server" if scope == "server" else "channels", channels,
                                                             minutes, reason)
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
        two = self.sec.config(interaction.guild.id)["approvals.two_person"]
        await self._ephemeral(interaction, "🔐 Lockdown requested",
                              ("A second staff member (Senior Moderator+) must approve it in the alert channel."
                               if two else "Confirm it in the alert channel.") + "\n\n**Preview**\n"
                              + "\n".join(f"• {x}" for x in preview[:12]))

    @lockdown_group.command(name="lift", description="End a lockdown now and restore what it changed (Moderator)")
    async def lockdown_lift(self, interaction: discord.Interaction, lockdown_id: Optional[int] = None):
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

    # ------------------------------------------------------------------ snapshots
    @snapshot_group.command(name="take", description="Snapshot roles, channels and permissions now (Senior Moderator)")
    async def snapshot_take(self, interaction: discord.Interaction):
        if not await self._guarded(interaction, "snapshot"):
            return
        snap_id = self.sec.take_snapshot(interaction.guild.id, f"manual by {interaction.user.id}")
        await self._ephemeral(interaction, "📸 Snapshot taken", f"Snapshot #{snap_id}.")

    @snapshot_group.command(name="list", description="Recent snapshots")
    async def snapshot_list(self, interaction: discord.Interaction):
        if not await self._guarded(interaction, "snapshot"):
            return
        rows = self.sec.store.snapshots(interaction.guild.id, 10)
        await self._ephemeral(interaction, "Snapshots", "\n".join(f"#{r['id']} · {E.ts(r['created'])} · {r['reason']}"
                                                                 for r in rows) or "None yet.")

    @snapshot_group.command(name="restore", description="Preview and request a restore from a snapshot (approval needed)")
    async def snapshot_restore(self, interaction: discord.Interaction, snapshot_id: Optional[int] = None):
        try:
            token, preview = await self.sec.request_restore(interaction.guild.id, interaction.user.id, snapshot_id)
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
        await self._ephemeral(interaction, "♻️ Restore requested", "Approve it in the alert channel (Admin).\n\n"
                              + "\n".join(f"• {x}" for x in preview[:15]))

    # ------------------------------------------------------------------ configuration
    async def _setting_names(self, interaction, current: str) -> list[app_commands.Choice[str]]:
        names = [s.key for s in SETTINGS if current.lower() in s.key]
        return [app_commands.Choice(name=n, value=n) for n in names[:25]]

    @config_group.command(name="view", description="Show the security settings (or one setting)")
    async def config_view(self, interaction: discord.Interaction, key: Optional[str] = None):
        if not await self._guarded(interaction, "posture"):
            return
        cfg = self.sec.config(interaction.guild.id)
        if key:
            setting = BY_KEY.get(key)
            if setting is None:
                return await self._ephemeral(interaction, "Unknown setting", key, Colors.FAIL)
            return await self._ephemeral(interaction, key, f"**{cfg[key]}** (default {setting.default})\n{setting.help}")
        groups: dict[str, list[str]] = {}
        for s in SETTINGS:
            groups.setdefault(s.key.split(".")[0] if "." in s.key else "general", []).append(
                f"`{s.key}` = **{cfg[s.key]}**" + ("" if cfg[s.key] == s.default else " ✱"))
        embed = discord.Embed(title=f"Security settings (config v{cfg.version})", color=Colors.SECURITY,
                              description="✱ = changed from the default. `/security config view key` explains one.")
        for name, lines in groups.items():
            embed.add_field(name=name, value="\n".join(lines)[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    config_view.autocomplete("key")(_setting_names)

    @config_group.command(name="set", description="Change one setting (Admin; versioned, can be rolled back)")
    async def config_set(self, interaction: discord.Interaction, key: str, value: app_commands.Range[str, 1, 40]):
        if not await self._guarded(interaction, "config"):
            return
        try:
            cfg = self.sec.config(interaction.guild.id).with_value(key, value)
        except ValueError as error:
            return await self._ephemeral(interaction, "Not changed", str(error), Colors.FAIL)
        cfg = self._save(interaction, cfg, f"{key}={value}")
        await self._ephemeral(interaction, f"Saved (config v{cfg.version})", f"`{key}` = **{cfg[key]}**")

    config_set.autocomplete("key")(_setting_names)

    @config_group.command(name="list", description="Edit a list: deny/allow domains, relaxed/protected/ignored channels")
    async def config_list(self, interaction: discord.Interaction,
                          name: Literal["links.deny", "links.allow", "channels.relaxed", "channels.protected",
                                        "channels.ignored"],
                          action: Literal["add", "remove", "show"], value: Optional[str] = None):
        cfg = self.sec.config(interaction.guild.id)
        if action == "show":
            if not await self._guarded(interaction, "posture"):
                return
            items = cfg[name]
            shown = [f"<#{x}>" if name.startswith("channels") else f"`{x}`" for x in items]
            return await self._ephemeral(interaction, name, ", ".join(shown) or "empty")
        if not await self._guarded(interaction, "config"):
            return
        if not value:
            return await self._ephemeral(interaction, "Missing value", "Give a domain or a channel id/mention.")
        item = value.strip().strip("<#>") if name.startswith("channels") else value.strip().lower()
        try:
            cfg = cfg.with_list(name, item, action == "add")
        except (ValueError, TypeError):
            return await self._ephemeral(interaction, "Not changed", "That isn't a valid entry.", Colors.FAIL)
        cfg = self._save(interaction, cfg, f"{name} {action} {item}")
        await self._ephemeral(interaction, f"Saved (config v{cfg.version})", f"{name}: {len(cfg[name])} entr(y/ies)")

    @config_group.command(name="history", description="Recent configuration versions")
    async def config_history(self, interaction: discord.Interaction):
        if not await self._guarded(interaction, "posture"):
            return
        rows = self.sec.store.config_history(interaction.guild.id)
        await self._ephemeral(interaction, "Config history", "\n".join(
            f"v{r['version']} · {E.ts(r['created'])} · <@{r['by']}> · {r['note']}" for r in rows) or "Defaults only.")

    @config_group.command(name="rollback", description="Restore an earlier configuration version (Admin)")
    async def config_rollback(self, interaction: discord.Interaction, version: app_commands.Range[int, 1, 10**6]):
        if not await self._guarded(interaction, "config"):
            return
        values = self.sec.store.config_version(interaction.guild.id, version)
        if values is None:
            return await self._ephemeral(interaction, "No such version", f"v{version}", Colors.FAIL)
        cfg = self._save(interaction, SecurityConfig(values), f"rollback to v{version}")
        await self._ephemeral(interaction, f"Rolled back (now v{cfg.version})", f"Settings are those of v{version}.")

    # ------------------------------------------------------------------ clearance
    @clearance_group.command(name="set", description="Give a role a clearance tier (Admin)")
    @app_commands.describe(tier="0 Member, 1 Helper, 2 Moderator, 3 Senior Moderator, 4 Admin; -1 removes the mapping")
    async def clearance_set(self, interaction: discord.Interaction, role: discord.Role, tier: app_commands.Range[int, -1, 4]):
        if not await self._guarded(interaction, "clearance"):
            return
        cfg = self.sec.config(interaction.guild.id).with_tier(role.id, None if tier < 0 else tier)
        cfg = self._save(interaction, cfg, f"clearance {role.id}={tier}")
        label = "removed" if tier < 0 else f"{Tier(tier).label} ({Tier(tier).rank})"
        await self._ephemeral(interaction, f"Clearance saved (config v{cfg.version})", f"{role.mention}: {label}")

    @clearance_group.command(name="view", description="Which roles hold which tier")
    async def clearance_view(self, interaction: discord.Interaction):
        cfg = self.sec.config(interaction.guild.id)
        lines = [f"<@&{rid}>: {TIER_LABELS[Tier(t)]}" for rid, t in sorted(cfg["tiers"].items(), key=lambda x: -x[1])]
        extra = ("Tiers also come from Discord permissions (Administrator/Manage Server = Admin, Ban Members = Senior "
                 "Moderator, Kick/Timeout Members = Moderator, Manage Messages = Helper)." if cfg["derive_tiers"]
                 else "Only the roles above count.")
        await self._ephemeral(interaction, "Clearance", "\n".join(lines + ["", extra]) if lines
                              else f"No roles mapped. {extra}")

    @clearance_group.command(name="check", description="A member's current clearance")
    async def clearance_check(self, interaction: discord.Interaction, member: discord.Member):
        tier = self.sec.tier_of(interaction.guild.id, member.id)
        await self._ephemeral(interaction, "Clearance", f"{member.mention}: **{tier.label}** ({tier.rank})")

    # ------------------------------------------------------------------ allowlist
    @allow_group.command(name="add", description="A time-limited exception (Senior Moderator)")
    @app_commands.describe(kind="What to exempt", value="User/role/channel id or a domain",
                           detectors="messages, joins, antinuke or all", days="Expires after this many days (max 90)")
    async def allow_add(self, interaction: discord.Interaction, kind: Literal["user", "role", "channel", "domain"],
                        value: str, detectors: Literal["messages", "joins", "antinuke", "all"],
                        days: app_commands.Range[int, 1, 90], note: Optional[str] = None):
        if not await self._guarded(interaction, "allowlist"):
            return
        value = value.strip().strip("<@&#!>").lower()
        if kind != "domain" and not value.isdigit():
            return await self._ephemeral(interaction, "Not an id", "Give a numeric id or a mention.", Colors.FAIL)
        expires = self.sec.now() + days * 86400
        entry = self.sec.store.add_allow(interaction.guild.id, kind, value, [detectors], expires,
                                         interaction.user.id, note or "")
        self.sec.runtime(interaction.guild.id).allows_at = 0
        await self._ephemeral(interaction, f"Exception #{entry} added", f"{kind} `{value}` exempt from {detectors} "
                              f"until {E.ts(expires)}")

    @allow_group.command(name="remove", description="Remove an exception (Senior Moderator)")
    async def allow_remove(self, interaction: discord.Interaction, entry_id: int):
        if not await self._guarded(interaction, "allowlist"):
            return
        removed = self.sec.store.remove_allow(interaction.guild.id, entry_id)
        self.sec.runtime(interaction.guild.id).allows_at = 0
        await self._ephemeral(interaction, "Removed" if removed else "Not found", f"Exception #{entry_id}")

    @allow_group.command(name="list", description="Current exceptions (all expire)")
    async def allow_list(self, interaction: discord.Interaction):
        if not await self._guarded(interaction, "posture"):
            return
        rows = self.sec.store.allows(interaction.guild.id)
        await self._ephemeral(interaction, "Exceptions", "\n".join(
            f"#{a['id']} {a['kind']} `{a['value']}` · {', '.join(a['detectors'])} · until {E.ts(a['expires'])}"
            for a in rows) or "None.")

    # ------------------------------------------------------------------ quarantine and probation
    @quarantine_group.command(name="list", description="Accounts quarantined by anti-nuke")
    async def quarantine_list(self, interaction: discord.Interaction):
        if not await self._guarded(interaction, "view_incidents"):
            return
        rows = self.sec.store.quarantined(interaction.guild.id)
        await self._ephemeral(interaction, "Quarantine", "\n".join(
            f"<@{q['user_id']}> · {E.ts(q['created'])} · {len(q['roles'])} role(s) held back · {q['reason'][:80]}"
            for q in rows) or "Nobody is quarantined.")

    @quarantine_group.command(name="release", description="Give a quarantined account its roles back (Admin)")
    async def quarantine_release(self, interaction: discord.Interaction, member: discord.Member):
        try:
            text = await self.sec.release_quarantine(interaction.guild.id, interaction.user.id, member.id)
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
        await self._ephemeral(interaction, "🔓 Quarantine released", text, Colors.SUCCESS)

    @probation_group.command(name="list", description="Members on probation and when it ends")
    async def probation_list(self, interaction: discord.Interaction):
        if not await self._guarded(interaction, "view_incidents"):
            return
        rows = sorted(self.sec.store.probation(interaction.guild.id), key=lambda r: r["until"])
        await self._ephemeral(interaction, f"Probation ({len(rows)})", "\n".join(
            f"<@{r['user_id']}> · ends {E.ts(r['until'])} · {r['reason'][:60]}" for r in rows[:25]) or "Nobody.")

    @probation_group.command(name="release", description="End a member's probation now (Helper)")
    async def probation_release(self, interaction: discord.Interaction, member: discord.Member):
        try:
            text = await self.sec.release_probation(interaction.guild.id, interaction.user.id, member.id)
        except Refused as refusal:
            return await self._ephemeral(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
        await self._ephemeral(interaction, "Probation", text, Colors.SUCCESS)

    # ------------------------------------------------------------------ verification, AutoMod, drills, Q&A
    @security.command(name="verify-panel", description="Post the verification button in a channel (Admin)")
    async def verify_panel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await self._guarded(interaction, "verify_panel"):
            return
        if not self.sec.config(interaction.guild.id)["verified_role"]:
            return await self._ephemeral(interaction, "Set a verified role first", "/security setup verified_role:@role")
        embed = E.simple("✅ Verification", "Press the button to confirm you're a person and unlock the server.",
                         Colors.SECURITY)
        await channel.send(embed=embed, view=verify_view())
        await self._ephemeral(interaction, "Posted", f"Verification panel posted in {channel.mention}.")

    @security.command(name="automod", description="Preview or create Rudhabot's recommended native AutoMod rules (Admin)")
    async def automod(self, interaction: discord.Interaction, action: Literal["preview", "create"] = "preview"):
        if not await self._guarded(interaction, "automod_sync"):
            return
        cfg = self.sec.config(interaction.guild.id)
        rules = automod_plan(cfg)
        lines = [f"• **{name}**: {what}" for name, what, _ in rules]
        if action == "preview":
            return await self._ephemeral(interaction, "AutoMod rules (preview)", "\n".join(lines)
                                         + "\n\nRun again with `action: create` to add them. Existing rules with the "
                                           "same name are left alone.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        created, failed = await create_automod_rules(interaction.guild, rules, cfg["alert_channel"])
        await self._ephemeral(interaction, "AutoMod", f"Created: {', '.join(created) or 'none'}"
                              + (f"\nNot created: {'; '.join(failed)}" if failed else ""))

    @security.command(name="drill", description="Run a simulated attack in a sandbox and see how security responds")
    async def drill(self, interaction: discord.Interaction,
                    scenario: Literal["join_raid", "spam_raid", "phishing", "mention_bomb", "staff_nuke",
                                      "malicious_bot", "webhook_spam", "slow_drip"]):
        if not await self._guarded(interaction, "drill"):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        from rudhabot.security.drill import run_drill
        models = self.sec.models if self.sec.models is not None and self.sec.models.loaded else None
        report = await asyncio.to_thread(run_drill, scenario, self.sec.config(interaction.guild.id), None, models)
        await interaction.followup.send(embed=E.simple(f"🎯 Drill: {scenario.replace('_', ' ')}", report,
                                                       Colors.SECURITY), ephemeral=True)

    @security.command(name="ask", description="Ask about the server's security state (read-only; staff)")
    async def ask(self, interaction: discord.Interaction, question: app_commands.Range[str, 3, 400]):
        if not await self._guarded(interaction, "ask"):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        text, source = await self.sec.ask(interaction.guild.id, question)
        await self._ephemeral(interaction, "Security Q&A" + (" (AI, read-only)" if source == "llm" else ""), text)

    @security.command(name="models", description="Clairvoyance security models: versions, health, switches")
    async def models(self, interaction: discord.Interaction):
        if not await self._guarded(interaction, "posture"):
            return
        status = self.sec.status(interaction.guild.id).get("models", {})
        lines = [f"message model: {status.get('message', 'unavailable')}",
                 f"incident model: {status.get('incident', 'unavailable')}",
                 f"alert ranker: {status.get('ranker', 'unavailable')}",
                 f"versions: {', '.join(f'{k}={v}' for k, v in status.get('versions', {}).items()) or 'none'}",
                 f"parameters: {status.get('params', {})}", f"calls: {status.get('stats', {})}",
                 "Turn one off: `/security config set models.message off`. Models only add evidence or rank alerts."]
        await self._ephemeral(interaction, "🔮 Clairvoyance (security)", "\n".join(lines))


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

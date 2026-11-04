"""Moderation commands. Each one checks the moderator's clearance when it runs (see security/clearance.py), refuses to
act on anyone of equal or higher rank, records a numbered case in the moderation log, and reports exactly what
Discord accepted.

/warn  /timeout  /untimeout  /kick  /ban  /massban  /unban  /purge  /slowmode
/case view | edit | delete       /modlog <member> (the member file: standing, cases, incidents, signals, holds)
"Report to moderators" (message context menu): any member can flag a message for staff.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.security.clearance import Refused, check
from rudhabot.security.reasons import duration
from rudhabot.ui.security_embeds import case_embed, member_file_embed, simple
from rudhabot.ui.theme import Colors

log = logging.getLogger("rudhabot.moderation")
REASON = app_commands.Range[str, 1, 400]


class ModerationCog(ServiceCog):
    case = app_commands.Group(name="case", description="Moderation cases: view, correct or remove a record",
                              guild_only=True)

    def __init__(self, bot):
        super().__init__(bot)
        self.security = self.services.security
        self.report_menu = app_commands.ContextMenu(name="Report to moderators", callback=self.report_message)
        self.report_menu.guild_only = True

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.report_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.report_menu.name, type=self.report_menu.type)

    # ------------------------------------------------------------------ helpers
    async def _say(self, interaction: discord.Interaction, title: str, text: str, color: int = Colors.MODLOG,
                   ephemeral: bool = True) -> None:
        embed = simple(title, text, color)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    async def _refused(self, interaction: discord.Interaction, refusal: Exception) -> None:
        await self._say(interaction, "⛔ Not allowed", str(refusal), Colors.UNAVAILABLE)

    async def _outcome(self, interaction, verb: str, result, cases: list, notify_user=None, dm_text: str = "") -> None:
        lines = []
        for case in cases:
            lines.append(f"✅ <@{case['target_id']}> · case #{case['case_no']}")
        for target, why in result.failed:
            lines.append(f"❌ <@{target}>: {why}")
        if not lines:
            lines.append(result.detail or result.status)
        await self._say(interaction, f"{verb}: {result.status}", "\n".join(lines)[:3900],
                        Colors.SUCCESS if result.ok else Colors.FAIL, ephemeral=False)
        if notify_user is not None and cases and dm_text:
            await self.services.messenger.dm(notify_user, embed=simple("Moderation notice", dm_text, Colors.MODLOG))

    async def _run(self, interaction, kind: str, targets: list[int], reason: str, **kw):
        try:
            return await self.security.moderate(interaction.guild.id, interaction.user.id, kind, targets,
                                                 reason=reason, **kw)
        except Refused as refusal:
            await self._refused(interaction, refusal)
            return None, None

    # ------------------------------------------------------------------ commands
    @app_commands.command(name="warn", description="Warn a member (recorded as a case; they get a DM)")
    @app_commands.guild_only()
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: REASON):
        try:
            case = await self.security.warn(interaction.guild.id, interaction.user.id, member.id, reason)
        except Refused as refusal:
            return await self._refused(interaction, refusal)
        await self._say(interaction, f"⚠️ Warned {member.display_name}", f"Case #{case['case_no']}: {reason}",
                        ephemeral=False)
        await self.services.messenger.dm(member, embed=simple(
            "Moderation notice", f"You were warned in **{interaction.guild.name}**: {reason}", Colors.MODLOG))

    @app_commands.command(name="timeout", description="Time a member out (Helpers: up to 10 minutes)")
    @app_commands.describe(minutes="1 minute to 28 days (40320 minutes)")
    @app_commands.guild_only()
    async def timeout(self, interaction: discord.Interaction, member: discord.Member,
                      minutes: app_commands.Range[int, 1, 40320], reason: REASON):
        result, cases = await self._run(interaction, "timeout", [member.id], reason, seconds=minutes * 60)
        if result is not None:
            await self._outcome(interaction, f"⏱️ Timeout ({duration(minutes * 60)})", result, cases, member,
                                f"You were timed out in **{interaction.guild.name}** for {duration(minutes * 60)}: {reason}")

    @app_commands.command(name="untimeout", description="Lift a member's timeout")
    @app_commands.guild_only()
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member, reason: REASON = "lifted"):
        result, cases = await self._run(interaction, "untimeout", [member.id], reason)
        if result is not None:
            await self._outcome(interaction, "▶️ Timeout lifted", result, cases)

    @app_commands.command(name="kick", description="Kick a member (Moderator)")
    @app_commands.guild_only()
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: REASON):
        await self.services.messenger.dm(member, embed=simple(
            "Moderation notice", f"You were kicked from **{interaction.guild.name}**: {reason}", Colors.MODLOG))
        result, cases = await self._run(interaction, "kick", [member.id], reason)
        if result is not None:
            await self._outcome(interaction, "👢 Kick", result, cases)

    @app_commands.command(name="ban", description="Ban a user, member or not (Senior Moderator)")
    @app_commands.describe(delete_hours="Also delete their messages from the last N hours (0-168)")
    @app_commands.guild_only()
    async def ban(self, interaction: discord.Interaction, user: discord.User, reason: REASON,
                  delete_hours: app_commands.Range[int, 0, 168] = 0):
        result, cases = await self._run(interaction, "ban", [user.id], reason, delete_seconds=delete_hours * 3600)
        if result is not None:
            await self._outcome(interaction, "🔨 Ban", result, cases)
            if result.ok:
                await self.security.note_ban(interaction.guild.id, user.id, user.name,
                                             user.avatar.key if user.avatar else None)

    @app_commands.command(name="massban", description="Ban many accounts by id at once (Senior Moderator; raid clean-up)")
    @app_commands.describe(user_ids="Ids separated by spaces or commas (up to 200)")
    @app_commands.guild_only()
    async def massban(self, interaction: discord.Interaction, user_ids: app_commands.Range[str, 5, 4000], reason: REASON,
                      delete_hours: app_commands.Range[int, 0, 168] = 1):
        ids = []
        for part in user_ids.replace(",", " ").split():
            if part.strip("<@!>").isdigit():
                ids.append(int(part.strip("<@!>")))
        ids = list(dict.fromkeys(ids))[:200]
        if not ids:
            return await self._say(interaction, "Nothing to do", "No valid user ids given.", Colors.FAIL)
        await interaction.response.defer(thinking=True)
        try:
            out = await self.security.massban(interaction.guild.id, interaction.user.id, ids, reason,
                                              delete_hours * 3600)
        except Refused as refusal:
            return await self._refused(interaction, refusal)
        lines = [f"{len(out['banned'])} banned" + (f" (cases #{out['cases'][0]['case_no']}-#{out['cases'][-1]['case_no']})"
                                                   if out["cases"] else "")]
        if out["failed"]:
            lines.append(f"{len(out['failed'])} not banned (staff, Rudhabot, or Discord refused)")
        if out["awaiting"]:
            lines.append(f"{out['awaiting']} established account(s) need a second Senior Moderator's approval "
                         "in the alert channel")
        await self._say(interaction, "🔨 Mass ban", "\n".join(lines), Colors.SUCCESS if out["banned"] else Colors.MODLOG,
                        ephemeral=False)

    @app_commands.command(name="unban", description="Lift a ban (Senior Moderator)")
    @app_commands.guild_only()
    async def unban(self, interaction: discord.Interaction, user_id: app_commands.Range[str, 15, 22], reason: REASON):
        if not user_id.isdigit():
            return await self._say(interaction, "Not an id", "Give the user's numeric id.", Colors.FAIL)
        result, cases = await self._run(interaction, "unban", [int(user_id)], reason)
        if result is not None:
            await self._outcome(interaction, "🕊️ Unban", result, cases)

    @app_commands.command(name="purge", description="Delete recent messages in this channel (Helpers: up to 50)")
    @app_commands.guild_only()
    async def purge(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 500],
                    member: Optional[discord.Member] = None, reason: REASON = "clean-up"):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            removed = await self.security.purge(interaction.guild.id, interaction.user.id, interaction.channel.id,
                                                count, member.id if member else None, reason)
        except Refused as refusal:
            return await self._refused(interaction, refusal)
        except Exception as error:
            return await self._say(interaction, "Purge failed", str(error)[:300], Colors.FAIL)
        await self._say(interaction, "🧹 Purged", f"{removed} message(s) removed.")

    @app_commands.command(name="slowmode", description="Set slowmode on a channel, optionally lifting it after a while")
    @app_commands.describe(seconds="0 turns it off (max 21600)", minutes="Lift automatically after this many minutes")
    @app_commands.guild_only()
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600],
                       channel: Optional[discord.TextChannel] = None, minutes: app_commands.Range[int, 0, 1440] = 0):
        target = channel or interaction.channel
        try:
            result = await self.security.slowmode(interaction.guild.id, interaction.user.id, target.id, seconds, minutes)
        except Refused as refusal:
            return await self._refused(interaction, refusal)
        await self._say(interaction, f"🐢 Slowmode: {result.status}", f"{target.mention}: {seconds}s"
                        + (f", lifted {duration(minutes * 60)} from now" if minutes else ""), ephemeral=False)

    # ------------------------------------------------------------------ cases
    def _need(self, interaction, action: str) -> None:
        check(int(self.security.tier_of(interaction.guild.id, interaction.user.id)), action)

    @case.command(name="view", description="Show one moderation case")
    async def case_view(self, interaction: discord.Interaction, number: app_commands.Range[int, 1, 10**9]):
        try:
            self._need(interaction, "view_cases")
        except Refused as refusal:
            return await self._refused(interaction, refusal)
        case = self.security.store.case(interaction.guild.id, number)
        if case is None:
            return await self._say(interaction, "No such case", f"There is no case #{number}.", Colors.FAIL)
        await interaction.response.send_message(embed=case_embed(case), ephemeral=True)

    @case.command(name="edit", description="Correct a case's reason (Moderator)")
    async def case_edit(self, interaction: discord.Interaction, number: app_commands.Range[int, 1, 10**9], reason: REASON):
        try:
            self._need(interaction, "case_edit")
        except Refused as refusal:
            return await self._refused(interaction, refusal)
        case = self.security.store.case(interaction.guild.id, number)
        if case is None:
            return await self._say(interaction, "No such case", f"There is no case #{number}.", Colors.FAIL)
        extra = dict(case["extra"])
        extra.setdefault("edits", []).append({"by": interaction.user.id, "old": case["reason"][:200]})
        updated = self.security.store.update_case(interaction.guild.id, number, reason=reason, extra=extra)
        await interaction.response.send_message(embed=case_embed(updated), ephemeral=True)

    @case.command(name="delete", description="Remove a case record entirely (Admin)")
    async def case_delete(self, interaction: discord.Interaction, number: app_commands.Range[int, 1, 10**9]):
        try:
            self._need(interaction, "case_delete")
        except Refused as refusal:
            return await self._refused(interaction, refusal)
        removed = self.security.store.delete_case(interaction.guild.id, number)
        await self._say(interaction, "Case removed" if removed else "No such case",
                        f"Case #{number} {'was deleted' if removed else 'does not exist'}.")

    @app_commands.command(name="modlog", description="A member's file: standing, cases, incidents and holds (Helpers+)")
    @app_commands.guild_only()
    async def modlog(self, interaction: discord.Interaction, user: discord.User):
        try:
            self._need(interaction, "view_cases")
        except Refused as refusal:
            return await self._refused(interaction, refusal)
        gid = interaction.guild.id
        joined = getattr(user, "joined_at", None)
        joined_ts = joined.timestamp() if joined else None
        standing = self.services.standing.of(gid, user.id, joined_ts)
        hold = self.security.economy_hold(gid, user.id, joined_ts)
        await interaction.response.send_message(embed=member_file_embed(user, standing, hold), ephemeral=True)

    # ------------------------------------------------------------------ member reports
    async def report_message(self, interaction: discord.Interaction, message: discord.Message):
        if message.author.bot or message.author.id == interaction.user.id:
            return await self._say(interaction, "Not reported", "You can't report that message.", Colors.UNAVAILABLE)
        from rudhabot.security.discord_port import message_event
        event = message_event(message, lambda m: self.security.tier_of(interaction.guild.id, m.id))
        created = getattr(interaction.user, "created_at", None)
        new = created is not None and (discord.utils.utcnow() - created).total_seconds() < 86400
        try:
            incident = await self.security.report(interaction.guild.id, interaction.user.id, event, reporter_new=new)
        except Refused as refusal:
            return await self._say(interaction, "Not reported", str(refusal), Colors.UNAVAILABLE)
        await self._say(interaction, "Thanks: reported", "The moderators have been told. Your name isn't shown to the "
                        "member you reported." + (f" (reference {incident})" if incident else ""), Colors.SUCCESS)

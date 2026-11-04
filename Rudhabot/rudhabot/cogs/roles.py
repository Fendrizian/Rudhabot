"""Temporary role tools: let Admins change roles through Rudhabot when their own role sits too low to do it in Discord.

/role add <member> <role> [reason]
/role remove <member> <role> [reason]
/role move <role> [position | above | below] [reason]
/role list                                  the hierarchy, and whether Rudhabot itself can manage roles

Admin clearance (checked on every use). Rudhabot can only touch roles below its own highest role, never @everyone or
roles managed by integrations. Every change is a case in the mod log, counts against the requesting admin's anti-nuke
budget, and granting a role with dangerous permissions alerts staff. Switch off with RUDHABOT_ROLE_TOOLS=0.
"""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.security.clearance import Refused
from rudhabot.ui.security_embeds import simple
from rudhabot.ui.theme import Colors

REASON = app_commands.Range[str, 1, 300]


class RolesCog(ServiceCog):
    role = app_commands.Group(name="role", description="Temporary role tools for Admins (assign, remove, move, list)",
                              guild_only=True)

    def __init__(self, bot):
        super().__init__(bot)
        self.security = self.services.security

    async def _reply(self, interaction: discord.Interaction, title: str, text: str, color: int) -> None:
        embed = simple(title, text, color)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _run(self, interaction: discord.Interaction, title: str, call) -> None:
        try:
            message, case = await call
        except Refused as refusal:
            return await self._reply(interaction, "⛔ Not done", str(refusal), Colors.UNAVAILABLE)
        await self._reply(interaction, title, message, Colors.SUCCESS if case else Colors.SECURITY)

    @role.command(name="add", description="Give a member a role (Admin; works below Rudhabot's own role)")
    async def add(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role,
                  reason: REASON = "role tools"):
        await self._run(interaction, "🎖️ Role given", self.security.role_assign(
            interaction.guild.id, interaction.user.id, member.id, role.id, True, reason))

    @role.command(name="remove", description="Take a role from a member (Admin)")
    async def remove(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role,
                     reason: REASON = "role tools"):
        await self._run(interaction, "🎖️ Role removed", self.security.role_assign(
            interaction.guild.id, interaction.user.id, member.id, role.id, False, reason))

    @role.command(name="move", description="Move a role in the hierarchy: to a position, or just above/below a role")
    @app_commands.describe(position="Exact position (1 = just above @everyone)", above="Place it just above this role",
                           below="Place it just below this role")
    async def move(self, interaction: discord.Interaction, role: discord.Role,
                   position: Optional[app_commands.Range[int, 1, 250]] = None, above: Optional[discord.Role] = None,
                   below: Optional[discord.Role] = None, reason: REASON = "role tools"):
        await self._run(interaction, "↕️ Role moved", self.security.role_move(
            interaction.guild.id, interaction.user.id, role.id, position=position,
            above=above.id if above else None, below=below.id if below else None, reason=reason))

    @role.command(name="list", description="The role hierarchy, and whether Rudhabot can manage roles")
    async def list_roles(self, interaction: discord.Interaction):
        try:
            info = self.security.role_overview(interaction.guild.id, interaction.user.id)
        except Refused as refusal:
            return await self._reply(interaction, "⛔ Not allowed", str(refusal), Colors.UNAVAILABLE)
        head = [f"Rudhabot's highest role: **{info['bot_role'] or '?'}** at position {info['bot_position']} · "
                f"Manage Roles: {'yes' if info['manage_roles'] else '**no**'} · "
                f"Administrator: {'yes' if info['administrator'] else 'no'}"]
        if not info["enabled"]:
            head.append("Role tools are **off** (RUDHABOT_ROLE_TOOLS=0).")
        if not info["manage_roles"]:
            head.append("Without Manage Roles, Rudhabot can't change any role: the server owner must grant it.")
        lines = []
        for r in info["roles"][:40]:
            mark = "✅" if r["manageable"] else "🔒"
            extra = " · integration" if r["managed"] else ""
            danger = " ⚠️" if r["dangerous"] else ""
            lines.append(f"{mark} `{r['position']:>3}` <@&{r['id']}>{danger} · {r['members']} member(s){extra}")
        if len(info["roles"]) > 40:
            lines.append(f"... and {len(info['roles']) - 40} more")
        text = "\n".join(head) + "\n\n✅ Rudhabot can manage · 🔒 it can't · ⚠️ dangerous permissions\n" + "\n".join(lines)
        await self._reply(interaction, "🎖️ Role hierarchy", text[:4000], Colors.SECURITY)

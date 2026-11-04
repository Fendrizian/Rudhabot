"""Temporary role tools: let Admins change roles through Rudhabot when their own role sits too low to do it in Discord.

/role add <member> <role> [reason]
/role remove <member> <role> [reason]
/role move <role> [position | above | below] [reason]
/role perms <role> [permission] [allow | deny] [reason]   without a permission: show the role's permissions
/role channel <channel|category> <member|role> [permission] [allow | deny | neutral] [reason]
/role list                                  the hierarchy, and whether Rudhabot itself can manage roles

Admin clearance (checked on every use). Rudhabot can only touch roles below its own highest role, never @everyone or
roles managed by integrations. Every change is a case in the mod log, counts against the requesting admin's anti-nuke
budget, and granting a role with dangerous permissions alerts staff. Switch off with RUDHABOT_ROLE_TOOLS=0.
"""
from __future__ import annotations

from typing import Literal, Optional, Union

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.security import perms as P
from rudhabot.security.clearance import Refused
from rudhabot.ui.security_embeds import simple
from rudhabot.ui.theme import Colors

REASON = app_commands.Range[str, 1, 300]
#: Discord's real names where they differ from the API flag ("manage_guild" is "Manage Server" in the app)
RENAMED = {"manage_guild": "Manage Server", "moderate_members": "Timeout Members",
           "mention_everyone": "Mention @everyone, @here and All Roles", "view_guild_insights": "View Server Insights",
           "send_tts_messages": "Send Text-to-Speech Messages", "use_external_emojis": "Use External Emoji",
           "create_polls": "Create Polls"}
#: discord.py accepts several names for some permissions; offer each permission once, under its current name
ALIASES = {"send_polls", "external_emojis", "external_stickers", "manage_emojis", "manage_emojis_and_stickers",
           "manage_permissions", "read_messages"}


def permission_names() -> dict[str, int]:
    """Every permission Discord knows (the API flag name -> its bit), without the aliases discord.py also accepts."""
    out, seen = {}, set()
    for name in sorted(set(discord.Permissions.VALID_FLAGS) - ALIASES):
        bit = discord.Permissions(**{name: True}).value
        if bit and not bit & (bit - 1) and bit not in seen:
            seen.add(bit)
            out[name] = bit
    return out


def label(name: str) -> str:
    return RENAMED.get(name) or name.replace("_", " ").title()


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

    async def _permission_choices(self, interaction: discord.Interaction, current: str):
        current = current.lower().replace(" ", "_")
        names = permission_names()
        hits = [n for n in names if current in n or current in label(n).lower().replace(" ", "_")]
        return [app_commands.Choice(name=label(n) + (" ⚠️" if names[n] & P.DANGEROUS else ""), value=n)
                for n in hits[:25]]

    @role.command(name="perms", description="Turn one of a role's permissions on or off, or show its permissions")
    @app_commands.describe(permission="Leave out to see the role's current permissions",
                           state="allow = turn it on, deny = turn it off")
    async def perms(self, interaction: discord.Interaction, role: discord.Role, permission: Optional[str] = None,
                    state: Optional[Literal["allow", "deny"]] = None, reason: REASON = "role tools"):
        guild_id, user_id = interaction.guild.id, interaction.user.id
        if permission is None:
            try:
                info = self.security.role_permissions_view(guild_id, user_id, role.id)
            except Refused as refusal:
                return await self._reply(interaction, "⛔ Not allowed", str(refusal), Colors.UNAVAILABLE)
            names = permission_names()
            if info["permissions"] & P.ADMINISTRATOR:
                body = "**Administrator** (every permission, ignoring channel overwrites)"
            else:
                on = [label(n) + (" ⚠️" if bit & P.DANGEROUS else "") for n, bit in names.items()
                      if info["permissions"] & bit]
                body = ", ".join(on) or "none"
            return await self._reply(interaction, f"🔑 {info['name']}: permissions",
                                     f"{body}\n\n⚠️ = dangerous. Change one with `/role perms role:… permission:… "
                                     f"state:allow|deny`.", Colors.SECURITY)
        names = permission_names()
        key = permission.strip().lower().replace(" ", "_")
        if key not in names:
            return await self._reply(interaction, "Unknown permission", f"`{permission}` isn't a Discord permission; "
                                     "pick one from the list.", Colors.FAIL)
        if state is None:
            return await self._reply(interaction, "Allow or deny?", "Choose `state: allow` or `state: deny`.",
                                     Colors.FAIL)
        await self._run(interaction, "🔑 Permission changed", self.security.role_permissions(
            guild_id, user_id, role.id, names[key], state == "allow", label(key), reason))

    perms.autocomplete("permission")(_permission_choices)

    @role.command(name="channel", description="Set one permission for a member or role in a channel or category")
    @app_commands.describe(channel="A channel or a whole category", target="The member or role",
                           permission="Leave out to see their current overwrite there",
                           state="allow, deny, or neutral (inherit from the role/category)")
    async def channel_perms(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel,
                            target: Union[discord.Member, discord.Role], permission: Optional[str] = None,
                            state: Optional[Literal["allow", "deny", "neutral"]] = None, reason: REASON = "role tools"):
        guild_id, user_id = interaction.guild.id, interaction.user.id
        target_type = "role" if isinstance(target, discord.Role) else "member"
        names = permission_names()
        if permission is None:
            try:
                info = self.security.channel_permissions_view(guild_id, user_id, channel.id, target.id, target_type)
            except Refused as refusal:
                return await self._reply(interaction, "⛔ Not allowed", str(refusal), Colors.UNAVAILABLE)
            allowed = ", ".join(label(n) for n, b in names.items() if info["allow"] & b) or "none"
            denied = ", ".join(label(n) for n, b in names.items() if info["deny"] & b) or "none"
            return await self._reply(interaction, f"🔐 {info['target']} in #{info['channel']}",
                                     f"**Allowed:** {allowed}\n**Denied:** {denied}\nEverything else is neutral "
                                     "(inherited).", Colors.SECURITY)
        key = permission.strip().lower().replace(" ", "_")
        if key not in names:
            return await self._reply(interaction, "Unknown permission", f"`{permission}` isn't a Discord permission; "
                                     "pick one from the list.", Colors.FAIL)
        if state is None:
            return await self._reply(interaction, "Allow, deny or neutral?", "Choose a `state`.", Colors.FAIL)
        await self._run(interaction, "🔐 Channel permission set", self.security.channel_permission(
            guild_id, user_id, channel.id, target.id, target_type, names[key], state, label(key), reason))

    channel_perms.autocomplete("permission")(_permission_choices)

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

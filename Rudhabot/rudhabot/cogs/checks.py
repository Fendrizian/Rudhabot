"""Reusable command checks. Failures raise errors that `RudhaTree.on_error` turns into the standard messages."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from rudhabot.errors import AlreadyInGame, NotAuthorized


def not_in_game():
    """Refuse the command while the user is inside a gambling game."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id in interaction.client.services.sessions:
            raise AlreadyInGame()
        return True
    return app_commands.check(predicate)


def banker_only():
    """Bankers (the banker role) and administrators."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.client.services.admin.is_banker(interaction.user):
            raise NotAuthorized()
        return True
    return app_commands.check(predicate)


def admin_only():
    """Only administrators (or the configured admin role) may run the command."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.client.services.admin.is_admin(interaction.user):
            raise NotAuthorized()
        return True
    return app_commands.check(predicate)


def economy_hold(interaction: discord.Interaction, *users) -> Optional[str]:
    """Why money can't move for the invoker or any of `users` right now (a security hold), or None. Holds stop raid
    and alt accounts from laundering money while security has them contained."""
    if not interaction.guild_id:
        return None
    security = interaction.client.services.security
    for user in (interaction.user, *users):
        joined = getattr(user, "joined_at", None)
        why = security.economy_hold(interaction.guild_id, user.id, joined.timestamp() if joined else None)
        if why:
            whose = "Your account" if user.id == interaction.user.id else f"{user.mention}'s account"
            after = ("It lifts by itself when the alert ends or the account is a day old." if "raid" in why else
                     "It lifts when a moderator releases the account (`/security release`).")
            return f"⛔ {whose} can't move money right now: {why}. {after}"
    return None

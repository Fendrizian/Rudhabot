"""Reusable command checks. Failures raise errors that `RudhaTree.on_error` turns into the standard messages."""
from __future__ import annotations

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


def admin_only():
    """Only administrators (or the configured admin role) may run the command."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.client.services.admin.is_admin(interaction.user):
            raise NotAuthorized()
        return True
    return app_commands.check(predicate)

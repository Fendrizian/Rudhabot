"""Small helpers for answering interactions and building the standard embeds."""
from __future__ import annotations

import discord

from rudhabot.ui.theme import Colors


def respond(interaction: discord.Interaction, embed: discord.Embed, ephemeral: bool = False):
    """Answer the interaction, using a follow-up if it was already acknowledged (e.g. after `defer`)."""
    if interaction.response.is_done():
        return interaction.followup.send(embed=embed, ephemeral=ephemeral)
    return interaction.response.send_message(embed=embed, ephemeral=ephemeral)


def author_embed(user, description: str = None, color: int = Colors.NEUTRAL, **kwargs) -> discord.Embed:
    """An embed with the invoking user in the author slot, as most commands present themselves."""
    embed = discord.Embed(description=description, color=color, **kwargs)
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


def error_embed(message: str, user=None) -> discord.Embed:
    embed = discord.Embed(description=message, color=Colors.ERROR)
    if user is not None:
        embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


def success_embed(message: str, user=None, color: int = Colors.SUCCESS) -> discord.Embed:
    embed = discord.Embed(description=message, color=color)
    if user is not None:
        embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed

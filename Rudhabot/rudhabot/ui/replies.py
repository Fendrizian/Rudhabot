"""Small helpers for answering interactions and building the standard embeds."""
from __future__ import annotations

from typing import Optional

import discord

from rudhabot.ui.theme import Colors


def respond(interaction: discord.Interaction, embed: discord.Embed, ephemeral: bool = False, *,
            content: Optional[str] = None, allowed_mentions: Optional[discord.AllowedMentions] = None):
    """Answer the interaction, using a follow-up if it was already acknowledged (e.g. after `defer`). `content` is a
    short line above the embed: embeds never ping, so a mention that should notify someone goes there."""
    extra = {}
    if content is not None:
        extra["content"] = content
    if allowed_mentions is not None:
        extra["allowed_mentions"] = allowed_mentions
    if interaction.response.is_done():
        return interaction.followup.send(embed=embed, ephemeral=ephemeral, **extra)
    return interaction.response.send_message(embed=embed, ephemeral=ephemeral, **extra)


def author_embed(user, description: str = None, color: int = Colors.GAMBLING, **kwargs) -> discord.Embed:
    """An embed with the invoking user in the author slot, as most commands present themselves."""
    embed = discord.Embed(description=description, color=color, **kwargs)
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


def error_embed(message: str, user=None) -> discord.Embed:
    embed = discord.Embed(description=message, color=Colors.FAIL)
    if user is not None:
        embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


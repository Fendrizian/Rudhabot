"""Shared by the never-expiring menus (/help, /guide): who a click is for, and showing them the page.

The person who opened the message navigates it in place; anyone else who clicks gets their own private copy, rendered
for them, so a shared message is never changed under its owner and nobody sees another person's view.
"""
from __future__ import annotations

from typing import Callable, Optional

import discord
from discord import Interaction, ui


def is_mine(interaction: Interaction) -> bool:
    """Whether the clicker owns the message (or it is already private to them)."""
    message = getattr(interaction, "message", None)
    meta = getattr(message, "interaction_metadata", None) if message is not None else None
    owner = getattr(getattr(meta, "user", None), "id", None)
    return (owner is None or owner == interaction.user.id
            or bool(getattr(message, "flags", None) and message.flags.ephemeral))


async def show_for_clicker(interaction: Interaction, embed: discord.Embed, view: ui.View,
                           upload: Optional[Callable[[], Optional[discord.File]]] = None) -> None:
    """Edit the message for its owner, or send anyone else a private copy. `upload` makes the page's image file,
    if it has one (Discord keeps attachments across edits, so an edit always sets them explicitly)."""
    image = upload() if upload is not None else None
    if is_mine(interaction):
        await interaction.response.edit_message(embed=embed, view=view, attachments=[image] if image else [])
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True,
                                                **({"file": image} if image else {}))

"""The /help command: a sectioned overview, a single section, or the full list, showing only what the viewer can use.

Staff see their sections (Banker, Admin, Moderation, Security) in a private reply, so those never appear to members
in a channel; members get the usual public reply. The section option autocompletes to the viewer's own sections.
"""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.services.permissions import viewer_for
from rudhabot.views.help import NavButton, SectionSelect, image_upload, render, visible_sections

FULL = ("full", "Full list (all your commands)")


class HelpCog(ServiceCog):
    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(SectionSelect, NavButton)

    async def _sections(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        viewer = viewer_for(self.services, interaction.user, interaction.guild_id)
        options = [(key, title) for key, title, *_ in visible_sections(viewer)] + [FULL]
        text = current.casefold()
        return [app_commands.Choice(name=title, value=key) for key, title in options
                if text in title.casefold() or text in key][:25]

    @app_commands.command(name="help", description="Show the commands you can use, by section or as a full list")
    @app_commands.describe(section="Jump straight to one section, or the full list")
    @app_commands.autocomplete(section=_sections)
    async def help_slash(self, interaction: discord.Interaction, section: Optional[str] = None):
        viewer = viewer_for(self.services, interaction.user, interaction.guild_id)
        target = getattr(section, "value", section) or "home"       # a Choice (tests, old clients) or its value
        embed, view = render(target, viewer=viewer)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=viewer.staff,
                                                **image_upload(embed))   # banner: main page only

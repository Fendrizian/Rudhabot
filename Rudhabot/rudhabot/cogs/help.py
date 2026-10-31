"""The /help command: a sectioned overview, a single section, or the full list."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.views.help import SECTIONS, NavButton, SectionSelect, render

SECTION_CHOICES = ([app_commands.Choice(name=f"{title}", value=key) for key, title, *_ in SECTIONS]
                   + [app_commands.Choice(name="Full list (all commands)", value="full")])


class HelpCog(ServiceCog):
    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(SectionSelect, NavButton)

    @app_commands.command(name="help", description="Show Rudhabot's commands, by section or as a full list")
    @app_commands.describe(section="Jump straight to one section, or the full list")
    @app_commands.choices(section=SECTION_CHOICES)
    async def help_slash(self, interaction: discord.Interaction, section: Optional[app_commands.Choice[str]] = None):
        embed, view = render(section.value if section else "home")
        await interaction.response.send_message(embed=embed, view=view)

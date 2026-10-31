"""The /help command."""
from __future__ import annotations

import math

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.views.help import HelpView, create_help_embed, get_commands_data


class HelpCog(ServiceCog):
    @app_commands.command(name="help", description="Show all available commands and their usage")
    async def help_slash(self, interaction: discord.Interaction):
        commands_data = get_commands_data()
        embed = create_help_embed(commands_data, 0, math.ceil(len(commands_data) / 5))
        await interaction.response.send_message(embed=embed, view=HelpView(commands_data, 0))

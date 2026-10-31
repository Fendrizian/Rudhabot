"""The /lottery information command. Tickets themselves are bought with /buy and drawn with /use."""
from __future__ import annotations

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import not_in_game
from rudhabot.ui import lottery_embeds
from rudhabot.ui.replies import respond


class LotteryCog(ServiceCog):
    @app_commands.command(name="lottery", description="View lottery tickets, prize odds and recent jackpot winners")
    @not_in_game()
    async def lottery(self, interaction: discord.Interaction):
        s = self.services
        inventory = self.users.get(interaction.user.id).get("inventory", {})
        embed = lottery_embeds.lottery_info(s.lottery.specs, s.catalog, inventory, s.lottery.recent_jackpots(5),
                                            s.settings.channels.lottery)
        await respond(interaction, embed)

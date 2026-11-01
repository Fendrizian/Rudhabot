"""Administrative commands. Every one of them requires the administrator permission or the admin role."""
from __future__ import annotations

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import admin_only, not_in_game
from rudhabot.ui.replies import error_embed
from rudhabot.ui.theme import CURRENCY, Colors

LOCATION_CHOICES = [app_commands.Choice(name="Wallet", value="wallet"), app_commands.Choice(name="Bank", value="bank")]


def _with_author(embed: discord.Embed, user) -> discord.Embed:
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


class AdminCog(ServiceCog):
    @app_commands.command(name="add-money", description="Add money to a user's wallet or bank")
    @app_commands.describe(target="The user to add money to", amount="Amount of money to add",
                           location="Where to add the money: wallet or bank")
    @app_commands.choices(location=LOCATION_CHOICES)
    @admin_only()
    @not_in_game()
    async def add_money(self, interaction: discord.Interaction, target: discord.User, amount: int, location: str = "wallet"):
        if amount <= 0:
            await interaction.response.send_message(embed=error_embed("❌ Amount must be positive.", interaction.user), ephemeral=True)
            return
        account = self.users.get(target.id)
        location = "wallet" if location == "wallet" else "bank"
        new_balance = account[location] + amount
        self.users.update(target.id, **{location: new_balance})
        embed = _with_author(discord.Embed(description=f"✅ Added {CURRENCY}{amount} to {target.mention}'s {location}.",
                                           color=Colors.SUCCESS), interaction.user)
        embed.set_footer(text=f"New {location} balance: {new_balance}ra")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="remove-money", description="Remove money from a user's wallet or bank")
    @app_commands.describe(target="The user to remove money from", amount="Amount of money to remove",
                           location="Where to remove the money from: wallet or bank")
    @app_commands.choices(location=LOCATION_CHOICES)
    @admin_only()
    @not_in_game()
    async def remove_money(self, interaction: discord.Interaction, target: discord.User, amount: int, location: str = "wallet"):
        if amount <= 0:
            await interaction.response.send_message(embed=error_embed("❌ Amount must be positive.", interaction.user), ephemeral=True)
            return
        account = self.users.get(target.id)
        location = "wallet" if location == "wallet" else "bank"
        current = account[location]
        if amount > current:
            await interaction.response.send_message(embed=error_embed(
                f"❌ Cannot remove {CURRENCY}{amount} from {target.mention}'s {location}. They only have {CURRENCY}{current}.",
                interaction.user))
            return
        self.users.update(target.id, **{location: current - amount})
        embed = _with_author(discord.Embed(description=f"✅ Removed {CURRENCY}{amount} from {target.mention}'s {location}.",
                                           color=Colors.SUCCESS), interaction.user)
        embed.set_footer(text=f"New {location} balance: {CURRENCY}{current - amount}")
        await interaction.response.send_message(embed=embed)

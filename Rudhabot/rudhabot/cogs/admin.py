"""Administrative commands. Every one of them requires the administrator permission or the admin role."""
from __future__ import annotations

from typing import Literal, Optional

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import admin_only, not_in_game
from rudhabot.ui.replies import error_embed
from rudhabot.ui.theme import CURRENCY, Colors
from rudhabot.ui.profile_embeds import profile_embed

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

    # ------------------------------------------------------------------ the server profile
    @app_commands.command(name="setup", description="Channels and roles Rudhabot uses here; empty shows the current setup")
    @app_commands.describe(reset="Put one setting (or all) back to its default",
                           admin_role="Needs the Administrator permission: this role gets admin powers")
    @app_commands.guild_only()
    @admin_only()
    async def setup(self, interaction: discord.Interaction,
                    notifications_channel: Optional[discord.TextChannel] = None,
                    lottery_channel: Optional[discord.TextChannel] = None,
                    birthdays_channel: Optional[discord.TextChannel] = None,
                    audit_channel: Optional[discord.TextChannel] = None,
                    reviews_channel: Optional[discord.TextChannel] = None,
                    admin_role: Optional[discord.Role] = None, banker_role: Optional[discord.Role] = None,
                    bankrupt_role: Optional[discord.Role] = None,
                    reset: Optional[Literal["notifications", "lottery", "birthdays", "audit", "reviews", "admin",
                                            "banker", "bankrupt", "all"]] = None):
        profile = self.services.profile
        wanted = {"notifications": notifications_channel, "lottery": lottery_channel, "birthdays": birthdays_channel,
                  "audit": audit_channel, "reviews": reviews_channel, "admin": admin_role, "banker": banker_role,
                  "bankrupt": bankrupt_role}
        wanted = {k: v for k, v in wanted.items() if v is not None}
        problems = []
        for key in ("admin", "banker", "bankrupt"):
            role = wanted.get(key)
            if role is None:
                continue
            if role.is_default() or role.managed:
                problems.append(f"`{key}`: {role.mention} can't be used (it's @everyone or a bot's own role).")
                wanted.pop(key)
        perms = getattr(interaction.user, "guild_permissions", None)
        if "admin" in wanted or reset in ("admin", "all"):
            if not (perms is not None and perms.administrator):
                problems.append("`admin`: only members with the Administrator permission can change the admin role.")
                wanted.pop("admin", None)
                reset = None if reset == "admin" else reset
        changes = {}
        if reset is not None:
            for key in ([reset] if reset != "all" else [e.key for e in profile.entries()]):
                if key == "admin" and not (perms is not None and perms.administrator):
                    continue
                old, new = profile.set(key, None, interaction.user.id)
                if old != new:
                    changes[key] = (old, new)
        for key in wanted:
            old, new = profile.set(key, wanted[key].id, interaction.user.id)
            if old != new:
                changes[key] = (old, new)
        if changes and interaction.guild is not None:
            self.services.security.profile_changed(interaction.guild.id, interaction.user.id, changes)
        sec = self.services.security.config(interaction.guild.id) if interaction.guild else None
        embed = profile_embed(profile, home=self.bot.guild_id, security=sec, changes=changes, problems=problems)
        await interaction.response.send_message(embed=embed, ephemeral=True)

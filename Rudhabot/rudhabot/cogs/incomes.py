"""Role incomes: /collect (your roles' incomes), /income (the full list), /set-income (Admin).

Every change to an income is answered privately and posted to the economy audit channel with who made it.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import admin_only, not_in_game, staff_only
from rudhabot.domain.cooldown import format_remaining
from rudhabot.services.incomes import MAX_AMOUNT, MAX_HOURS, MIN_HOURS, IncomeRefused, RoleIncome
from rudhabot.ui.replies import respond
from rudhabot.ui.theme import BANKICON, CURRENCY, Colors

log = logging.getLogger("rudhabot.incomes")
NONE = discord.AllowedMentions.none()


def _every(income: RoleIncome) -> str:
    hours = income.hours
    if hours % 168 == 0:
        return f"every {hours // 168} week(s)" if hours > 168 else "every week"
    if hours % 24 == 0:
        return f"every {hours // 24} days" if hours > 24 else "every day"
    return f"every {hours} h"


def _line(income: RoleIncome) -> str:
    return f"<@&{income.role_id}> · **{CURRENCY}{income.amount:,}** {_every(income)}"


def _wait(remaining) -> str:
    return format_remaining(remaining, hours=True)


class IncomeCog(ServiceCog):
    def _prune(self, guild: Optional[discord.Guild]) -> None:
        """Incomes for roles deleted from the server are dropped (once the server's roles are known)."""
        roles = getattr(guild, "roles", None)
        if guild is not None and roles:
            for income in self.services.incomes.prune([r.id for r in roles]):
                log.info("role income for deleted role %s removed", income.role_id)

    @app_commands.command(name="collect", description="Collect the income your roles earn")
    @app_commands.guild_only()
    @not_in_game()
    async def collect(self, interaction: discord.Interaction):
        self._prune(interaction.guild)
        roles = [r.id for r in getattr(interaction.user, "roles", [])]
        result = self.services.incomes.collect(interaction.user.id, roles)
        if not result.any_income:
            embed = discord.Embed(description="None of your roles has an income. See `/income` for the roles that do.",
                                  color=Colors.UNAVAILABLE)
            return await respond(interaction, embed, ephemeral=True)
        lines = [f"✅ <@&{i.role_id}> **+{CURRENCY}{i.amount:,}**" for i in result.paid]
        lines += [f"⏳ <@&{i.role_id}> ready in **{_wait(left)}**" for i, left in result.waiting]
        if result.paid:
            title = f"💼 You collected {CURRENCY}{result.total:,}"
            color = Colors.INCOME
        else:
            title, color = "⏳ Nothing to collect yet", Colors.COOLDOWN
        embed = discord.Embed(title=title, description="\n".join(lines), color=color)
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        if result.paid:
            embed.set_footer(text=f"Wallet: {result.wallet:,}ra")
        await respond(interaction, embed, ephemeral=not result.paid)

    @app_commands.command(name="income", description="Every role income, and when yours are ready")
    async def income(self, interaction: discord.Interaction):
        self._prune(interaction.guild)
        incomes = self.services.incomes
        mine = {r.id for r in getattr(interaction.user, "roles", [])}
        rows = []
        for income in incomes.incomes():
            text = _line(income)
            if income.role_id in mine:
                left = incomes.ready_in(interaction.user.id, income)
                text += " · ✅ ready: `/collect`" if left is None else f" · ⏳ yours in {_wait(left)}"
            rows.append(text)
        embed = discord.Embed(title=f"{BANKICON} Role incomes", color=Colors.INCOME,
                              description="\n".join(rows) or "No role has an income yet.")
        if rows:
            embed.set_footer(text="Collect the ones you hold with /collect. Each role has its own cooldown.")
        await respond(interaction, embed)

    @app_commands.command(name="set-income", description="Admin: give a role an income, change it, or remove it (0)")
    @app_commands.describe(role="The role that earns it", amount=f"ra per collection, up to {MAX_AMOUNT:,} (0 removes it)",
                           hours=f"How often it can be collected, {MIN_HOURS}-{MAX_HOURS} h (default: keep, or 24)")
    @app_commands.guild_only()
    @staff_only("admin")
    @admin_only()
    async def set_income(self, interaction: discord.Interaction, role: discord.Role,
                         amount: app_commands.Range[int, 0, MAX_AMOUNT],
                         hours: Optional[app_commands.Range[int, MIN_HOURS, MAX_HOURS]] = None):
        if role.is_default() or role.managed:
            return await self._refuse(interaction, f"{role.mention} can't have an income (it's @everyone or a bot's "
                                                   "own role).")
        try:
            old, new = self.services.incomes.set(role.id, amount, hours, by=interaction.user.id)
        except IncomeRefused as refusal:
            return await self._refuse(interaction, str(refusal))
        before = f"{CURRENCY}{old.amount:,} {_every(old)}" if old else "no income"
        after = f"{CURRENCY}{new.amount:,} {_every(new)}" if new else "no income"
        text = f"{role.mention}: {before} → **{after}**"
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ {text}", color=Colors.SUCCESS),
                                                ephemeral=True)
        log.info("admin %s changed the income of role %s: %s -> %s", interaction.user.id, role.id, before, after)
        trail = discord.Embed(title=f"{BANKICON} Role income changed", description=text, color=Colors.BANK)
        trail.set_footer(text=f"by {interaction.user} ({interaction.user.id})")
        await self.services.messenger.post(self.services.profile.channel("audit"), embed=trail, allowed_mentions=NONE)

    async def _refuse(self, interaction: discord.Interaction, message: str) -> None:
        await interaction.response.send_message(embed=discord.Embed(description=f"❌ {message}", color=Colors.FAIL),
                                                ephemeral=True)

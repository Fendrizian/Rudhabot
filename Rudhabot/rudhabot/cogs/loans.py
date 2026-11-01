"""Loan commands and the background jobs that collect overdue loans and warn about loans falling due."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import tasks

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import not_in_game
from rudhabot.services.loans import AutoPaid, LoanRefused
from rudhabot.ui.replies import respond
from rudhabot.ui.theme import CURRENCY, LOANDUEICON, Colors

log = logging.getLogger("rudhabot.loans")


def _embed(user, message: str, color: int = Colors.FAIL) -> discord.Embed:
    embed = discord.Embed(description=message, color=color)
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


def _author(embed: discord.Embed, user) -> discord.Embed:
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


class LoansCog(ServiceCog):
    async def cog_load(self):
        self.settle_loans.start()
        self.warn_loans.start()

    async def cog_unload(self):
        self.settle_loans.cancel()
        self.warn_loans.cancel()

    # ----------------------------------------------------------------- commands
    @app_commands.command(name="loan", description="Take a loan from the bank.")
    @app_commands.describe(amount="Amount to borrow (100–10000ra)", days="Number of days to repay (1–30)")
    @not_in_game()
    async def loan(self, interaction: discord.Interaction, amount: int, days: int):
        await interaction.response.defer()
        terms = self.services.settings.loans
        try:
            opened = self.services.loans.open(interaction.user.id, amount, days)
        except LoanRefused as refusal:
            if refusal.code == "already_has_loan":
                await respond(interaction, _embed(interaction.user, "❌ You already have an active loan."), ephemeral=True)
            elif refusal.code == "banned":
                until, remaining = refusal.data["until"], refusal.data["days_remaining"]
                await respond(interaction, _embed(interaction.user, 
                    f"🚫 You're banned from taking loans until **{until.strftime('%d %B %Y')}** ({remaining} days remaining)", Colors.UNAVAILABLE))
            else:
                await respond(interaction, _embed(interaction.user, 
                    f"Invalid loan parameters. \nAmount must be between {terms.min_amount} and {terms.max_amount}, "
                    f"days between {terms.min_days} and {terms.max_days}."), ephemeral=True)
            return
        embed = discord.Embed(title="<:loan_approved:1401355324103069867> Loan Approved", color=Colors.LOAN)
        embed.add_field(name="Amount Borrowed", value=f"{CURRENCY}{amount}", inline=True)
        embed.add_field(name="Due Date", value=f"{LOANDUEICON}{opened.due.strftime('%d %B %Y')}", inline=True)
        embed.add_field(name="Interest Rate",
                        value=f"<:loan_interest:1401358068238913557>{terms.interest_rate * 100:.2f}% per day", inline=True)
        embed.set_footer(text="You can extend your loan once for a 10% fee.")
        await respond(interaction, _author(embed, interaction.user))

    @app_commands.command(name="extend", description="Extend your loan for 10% fee.")
    @app_commands.describe(days="Number of days to extend (default 1)")
    @not_in_game()
    async def extend(self, interaction: discord.Interaction, days: int = 1):
        await interaction.response.defer()
        try:
            extended = self.services.loans.extend(interaction.user.id, days)
        except LoanRefused as refusal:
            data = refusal.data
            message = {
                "no_loan_to_extend": "❌ You don't have an active loan to extend.",
                "at_max_days": f"❌ You can't extend a {data.get('max_days')}-day loan.",
                "already_extended": "❌ You already extended this loan once.",
                "exceeds_limit": f"❌ You can only extend by {data.get('allowed')} more day(s) to reach the limit.",
                "cannot_afford_fee": f"❌ You need {CURRENCY}{data.get('fee')} in your wallet to extend.",
            }[refusal.code]
            await respond(interaction, _embed(interaction.user, message), ephemeral=True)
            return
        await respond(interaction, _embed(interaction.user, 
            f"{LOANDUEICON} Your loan has been extended by {extended.days} day(s) for a fee of {CURRENCY}{extended.fee}.\n", Colors.EXTEND))

    @app_commands.command(name="loaninfo", description="Check your current loan status and remaining debt.")
    async def loaninfo(self, interaction: discord.Interaction):
        loan = self.services.loans.active_loan(interaction.user.id)
        if not loan:
            await respond(interaction, _embed(interaction.user, "❌ You have no active loan."), ephemeral=True)
            return
        embed = discord.Embed(title="<:loan_info:1401356490031497216> Loan Status", color=Colors.LOAN_STATUS)
        embed.add_field(name="Borrowed", value=f"{CURRENCY}{loan.principal}", inline=True)
        embed.add_field(name="Duration", value=f"{loan.interest_days} days", inline=True)
        embed.add_field(name="Days Accrued", value=f"{loan.accrued_days()}", inline=True)
        embed.add_field(name="Total Due", value=f"{CURRENCY}{loan.total_due()}", inline=True)
        embed.add_field(name="Due Date", value=loan.due_at.strftime('%d %B %Y'), inline=True)
        embed.add_field(name="Extended", value="Yes" if loan.extended else "No", inline=True)
        await respond(interaction, _author(embed, interaction.user))

    @app_commands.command(name="pay", description="Pay off your loan debt.")
    @app_commands.describe(amount="Amount to pay or 'all'")
    @not_in_game()
    async def pay(self, interaction: discord.Interaction, amount: str):
        await interaction.response.defer()
        try:
            repayment = self.services.loans.repay(interaction.user.id, amount)
        except LoanRefused as refusal:
            if refusal.code == "no_loan_to_pay":
                await respond(interaction, _embed(interaction.user, "❌ You have no active loan to pay."), ephemeral=True)
            elif refusal.code == "invalid_amount":
                await respond(interaction, _embed(interaction.user, "❌ Invalid amount format. Use a number or 'all'."), ephemeral=True)
            elif refusal.code == "not_positive":
                await respond(interaction, _embed(interaction.user, "❌ Amount must be positive."), ephemeral=True)
            else:
                await respond(interaction, _embed(interaction.user, "❌ You don't have enough money in your wallet."))
            return
        if repayment.cleared:
            await respond(interaction, _embed(interaction.user, "✅ You have fully repaid your loan!", Colors.SUCCESS))
        else:
            await respond(interaction, _embed(interaction.user, 
                f"You paid {CURRENCY}{repayment.paid} towards your loan.\nRemaining debt: {CURRENCY}{repayment.remaining}", Colors.LOAN_PARTIAL))

    # ----------------------------------------------------------- background jobs
    def _notification_channel(self):
        guild = self.bot.primary_guild
        return guild, (guild.get_channel(self.services.settings.channels.notifications) if guild else None)

    @tasks.loop(minutes=5)
    async def settle_loans(self):
        try:
            guild, channel = self._notification_channel()
            events = self.services.loans.settle_due_loans()
        except Exception:
            log.exception("loan settlement pass failed")
            return
        for event in events:                    # the money is settled; one failed notice mustn't skip the others
            try:
                if isinstance(event, AutoPaid):
                    await self.services.loan_notices.auto_paid(event, channel)
                else:
                    await self.services.loan_notices.bankrupt(event, channel)
                    await self._assign_bankrupt_role(guild, event.user_id)
            except Exception:
                log.exception("loan notice for %s failed", event.user_id)

    @tasks.loop(hours=1)
    async def warn_loans(self):
        try:
            _, channel = self._notification_channel()
            warnings = self.services.loans.pending_warnings()
        except Exception:
            log.exception("loan warning pass failed")
            return
        for warning in warnings:
            try:
                await self.services.loan_notices.due_soon(warning, channel)
            except Exception:
                log.exception("loan warning for %s failed", warning.user_id)
            self.services.loans.mark_warned(warning.user_id)      # warn once, even if this one couldn't be delivered

    @settle_loans.before_loop
    @warn_loans.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()

    async def _assign_bankrupt_role(self, guild, user_id: int) -> None:
        if not guild:
            return
        member = guild.get_member(user_id)
        role = guild.get_role(self.services.settings.loans.bankrupt_role_id) if member else None
        if member and role:
            try:
                await member.add_roles(role)
            except Exception as error:
                log.warning("could not add the bankrupt role to %s: %s", user_id, error)

"""/banker: tools for the banker role (and administrators).

Bankers look after accounts; they cannot create or remove money (/add-money and /remove-money stay admin-only).

/banker account <user>              a member's full bank picture: balances, card, limits, loan, holds and bans
/banker card-hold <user> <reason>   decline a member's credit card until a banker releases it
/banker card-release <user>         lift a card hold
/banker loan-unban <user> <reason>  lift a bankruptcy loan ban early (and the bankrupt role)

Every action is answered privately and posted to the economy audit channel with who did it and why.
/audit and /review are banker commands too (cogs/audit.py).
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import banker_only
from rudhabot.services.bank import BankRefused
from rudhabot.services.loans import LoanRefused
from rudhabot.ui.replies import error_embed
from rudhabot.ui.theme import BANKICON, CURRENCY, Colors

log = logging.getLogger("rudhabot.banker")


class BankerCog(ServiceCog, commands.GroupCog, group_name="banker", group_description="Banker tools for member accounts"):
    async def _done(self, interaction: discord.Interaction, action: str, target: discord.abc.User, reason: str = "") -> None:
        """Confirm privately, then leave a public trail in the audit channel."""
        text = f"{action} for {target.mention}" + (f"\nReason: {reason}" if reason else "")
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ {text}", color=Colors.SUCCESS),
                                                ephemeral=True)
        log.info("banker %s: %s for %s (%s)", interaction.user.id, action, target.id, reason)
        embed = discord.Embed(title=f"{BANKICON} Banker action", description=text, color=Colors.BANK)
        embed.set_footer(text=f"by {interaction.user} ({interaction.user.id})")
        await self.services.messenger.post(self.services.settings.channels.audit, embed=embed,
                                           allowed_mentions=discord.AllowedMentions.none())

    async def _refuse(self, interaction: discord.Interaction, message: str) -> None:
        await interaction.response.send_message(embed=error_embed(message, interaction.user), ephemeral=True)

    @app_commands.command(name="account", description="Banker: a member's balances, card, limits, loan, holds and bans")
    @banker_only()
    async def account(self, interaction: discord.Interaction, user: discord.User):
        bank, loans = self.services.bank, self.services.loans
        profile = bank.account(user.id)
        acc = self.users.get(user.id)
        tier, credit, usage = bank.tier_of(profile), profile["credit"], profile["usage"]
        e = discord.Embed(title=f"{BANKICON} {user.display_name}'s account", color=Colors.BANK)
        e.add_field(name="Wallet", value=f"{CURRENCY}{acc.get('wallet', 0):,}")
        e.add_field(name="Bank", value=f"{CURRENCY}{acc.get('bank', 0):,}")
        e.add_field(name="Card", value=f"{tier.emoji} {tier.name}")
        e.add_field(name="This month", value=f"Deposits {usage['deposit']:,}/{tier.monthly_limit:,}\n"
                                              f"Withdrawals {usage['withdraw']:,}/{tier.monthly_limit:,}")
        status = ("⛔ on hold" if credit.get("hold") else "❄️ frozen (late statement)" if credit.get("frozen") else "active")
        e.add_field(name="Credit card", value=f"Owed {CURRENCY}{credit['balance']:,} of {tier.credit_limit:,}\n"
                                               + (f"Statement {credit['statement']:,} due {credit['due']}\n" if credit.get("statement") else "")
                                               + status)
        loan = loans.active_loan(user.id)
        e.add_field(name="Loan", value=(f"{CURRENCY}{loan.total_due():,} due {loan.due_at.strftime('%d %B %Y')}" if loan else "None"))
        if credit.get("hold"):
            hold = credit["hold"]
            e.add_field(name="Card hold", value=f"{hold['reason']}\nby <@{hold['by']}> on {hold['at'][:10]}", inline=False)
        ban = loans.ban_until(user.id)
        if ban:
            e.add_field(name="Loan ban", value=f"until {ban.strftime('%d %B %Y')}", inline=False)
        e.set_footer(text="Read-only · /banker card-hold · /banker card-release · /banker loan-unban")
        await interaction.response.send_message(embed=e, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="card-hold", description="Banker: decline a member's credit card until it is released")
    @app_commands.describe(reason="Shown to the member when their card is declined")
    @banker_only()
    async def card_hold(self, interaction: discord.Interaction, user: discord.User, reason: app_commands.Range[str, 3, 200]):
        try:
            self.services.bank.hold_card(user.id, reason, interaction.user.id)
        except BankRefused as error:
            return await self._refuse(interaction, str(error))
        await self._done(interaction, "⛔ Card put on hold", user, reason)

    @app_commands.command(name="card-release", description="Banker: lift a hold on a member's credit card")
    @banker_only()
    async def card_release(self, interaction: discord.Interaction, user: discord.User, reason: str = ""):
        try:
            self.services.bank.release_card(user.id)
        except BankRefused as error:
            return await self._refuse(interaction, str(error))
        await self._done(interaction, "Card hold lifted", user, reason)

    @app_commands.command(name="loan-unban", description="Banker: lift a member's loan ban early")
    @banker_only()
    async def loan_unban(self, interaction: discord.Interaction, user: discord.User, reason: app_commands.Range[str, 3, 200]):
        try:
            until = self.services.loans.lift_ban(user.id)
        except LoanRefused:
            return await self._refuse(interaction, "❌ That member has no loan ban.")
        await self._remove_bankrupt_role(interaction.guild, user.id)
        await self._done(interaction, f"Loan ban lifted (was until {until.strftime('%d %B %Y')})", user, reason)

    async def _remove_bankrupt_role(self, guild, user_id: int) -> None:
        member = guild.get_member(user_id) if guild else None
        role = guild.get_role(self.services.settings.loans.bankrupt_role_id) if member else None
        if member and role and role in getattr(member, "roles", ()):
            try:
                await member.remove_roles(role)
            except Exception as error:
                log.warning("could not remove the bankrupt role from %s: %s", user_id, error)

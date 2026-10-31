"""Builds and delivers the loan notices (due-soon warning, auto-payment, bankruptcy).

Every notice has a public channel embed (always) and a private DM embed (only when the user can be found)."""
from __future__ import annotations

import discord

from rudhabot.services.loans import AutoPaid, Bankruptcy, DueSoon
from rudhabot.ui.messenger import Messenger
from rudhabot.ui.theme import CURRENCY, LOANDUEICON, Colors

GREY = Colors.NOTIFICATIONS


class LoanNotifier:
    def __init__(self, messenger: Messenger):
        self._messenger = messenger

    async def due_soon(self, warning: DueSoon, channel) -> None:
        due = warning.due.strftime("%d %B %Y at %H:%M")

        def build(description: str, closing_field: tuple[str, str]) -> discord.Embed:
            embed = discord.Embed(title=f"{LOANDUEICON} Loan Payment Warning", description=description, color=GREY)
            embed.add_field(name="Total Amount Due", value=f"{CURRENCY}{warning.total_due}", inline=True)
            embed.add_field(name="Due Date", value=due, inline=True)
            embed.add_field(name=closing_field[0], value=closing_field[1], inline=False)
            embed.set_footer(text="Use /pay all to pay your loan • Use /loaninfo to check details")
            return embed

        mention = f"<@{warning.user_id}>"
        await self._deliver(
            warning.user_id,
            dm=build("Your loan payment is due in less than 24 hours!",
                     ("Important", "If you cannot pay, all your money will be taken and you'll be banned from loans!")),
            channel=channel,
            channel_embed=build(f"{mention}, your loan payment is due in less than 24 hours!",
                                ("Warning", "Pay your loan or face liquidation and loan ban!")),
        )

    async def auto_paid(self, event: AutoPaid, channel) -> None:
        def build(description: str, footer: str) -> discord.Embed:
            embed = discord.Embed(title="💳 Loan Auto-Paid", description=description, color=GREY)
            embed.add_field(name="Amount Deducted", value=f"{CURRENCY}{event.total_due}", inline=True)
            embed.add_field(name="Status", value="☑️ Loan Cleared", inline=True)
            embed.set_footer(text=footer)
            return embed

        await self._deliver(
            event.user_id,
            dm=build("Your overdue loan has been automatically paid from your balance.",
                     "Your loan has been successfully repaid!"),
            channel=channel,
            channel_embed=build(f"<@{event.user_id}>'s overdue loan has been automatically paid.",
                                "Auto-payment successful"),
        )

    async def bankrupt(self, event: Bankruptcy, channel) -> None:
        def build(description: str) -> discord.Embed:
            embed = discord.Embed(title="⛓️ Bankruptcy Notice", description=description, color=GREY)
            embed.add_field(name="Debt Amount", value=f"{CURRENCY}{event.total_due}", inline=True)
            embed.add_field(name="Ban Duration", value=f"{event.ban_days} days", inline=True)
            embed.add_field(name="Ban Until", value=event.ban_until.strftime("%d %B %Y"), inline=True)
            return embed

        dm = build("You were unable to pay your loan and have been declared bankrupt!")
        dm.add_field(name="Consequences",
                     value="• All money confiscated\n• Banned from taking loans\n• Bankrupt role assigned", inline=False)
        dm.set_footer(text="You can take loans again after the ban expires.")
        public = build(f"<@{event.user_id}> has been declared bankrupt due to unpaid loan!")
        public.add_field(name="Status", value="✖️ All assets liquidated • Loan ban active", inline=False)
        public.set_footer(text="Bankruptcy processed")
        await self._deliver(event.user_id, dm=dm, channel=channel, channel_embed=public)

    async def _deliver(self, user_id: int, *, dm: discord.Embed, channel, channel_embed: discord.Embed) -> None:
        user = await self._messenger.find_user(user_id)
        if user:
            await self._messenger.dm(user, embed=dm)
        if channel:
            await self._messenger.post(channel, content=f"<@{user_id}>", embed=channel_embed)

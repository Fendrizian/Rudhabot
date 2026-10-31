"""Crash: the multiplier climbs every 2 seconds and may crash; cash out before it does."""
from __future__ import annotations

import asyncio
import logging
import random

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.ui.theme import CURRENCY
from rudhabot.views.base import ServiceView

log = logging.getLogger("rudhabot.crash")


def _authored(embed: discord.Embed, user) -> discord.Embed:
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


def opening_embed(bet_amount: int, user) -> discord.Embed:
    return _authored(discord.Embed(
        title="🚀 Crash",
        description=f"Bet: {CURRENCY}{bet_amount}\n\nPress **Cash Out** before the multiplier crashes!",
        color=0x99AAB5,
    ), user)


class CrashView(ServiceView):
    def __init__(self, services, interaction: Interaction, bet_amount: int, rng=random):
        super().__init__(services, timeout=30)
        self.interaction = interaction
        self.user_id = interaction.user.id
        self.bet_amount = bet_amount
        self.rng = rng
        self.cashed_out = False
        self.crashed = False
        self.multiplier = 1.0
        self.running = True
        self.lock = asyncio.Lock()
        self.task = asyncio.create_task(self.run_crash())

    async def interaction_check(self, interaction: Interaction):
        return interaction.user.id == self.user_id

    @ui.button(label="Cash Out", style=ButtonStyle.primary)
    async def cashout(self, i: Interaction, b: ui.Button):
        async with self.lock:
            if self.cashed_out or self.crashed:
                return
            self.cashed_out = True
            self.running = False
            winnings = int(self.bet_amount * self.multiplier)
            self.services.users.credit_wallet(self.user_id, winnings)
            embed = discord.Embed(
                title="🚀 Crash",
                description=f"Multiplier: x{self.multiplier:.2f}\n\n🎉 You cashed out and won {CURRENCY}{winnings}",
                color=0x57F287,
            )
            await i.response.edit_message(embed=_authored(embed, i.user), view=None)
            self.services.sessions.end(self.user_id)

    async def run_crash(self):
        try:
            while self.running:
                await asyncio.sleep(2)
                self.multiplier += self.rng.uniform(0.1, 0.5)
                crash_chance = min(0.05 + self.multiplier * 0.07, 0.95)
                async with self.lock:
                    if not self.running:
                        break                                    # cashed out meanwhile
                    if self.rng.random() < crash_chance:
                        self.crashed = True
                        self.running = False
                        embed = discord.Embed(
                            title="🚀 Crash",
                            description=f"Multiplier crashed at x{self.multiplier:.2f}!\n\n💸 You lose {CURRENCY}{self.bet_amount}",
                            color=0xED4245,
                        )
                        await self.interaction.edit_original_response(embed=_authored(embed, self.interaction.user), view=None)
                        self.services.sessions.end(self.user_id)
                        break
                    embed = discord.Embed(
                        title="🚀 Crash",
                        description=f"Bet: {CURRENCY}{self.bet_amount}\nMultiplier: x{self.multiplier:.2f}\n\n"
                                    f"Press **Cash Out** before the multiplier crashes!",
                        color=0x99AAB5,
                    )
                    await self.interaction.edit_original_response(embed=_authored(embed, self.interaction.user), view=self)
        except Exception:
            log.exception("crash loop failed for user %s", self.user_id)

    async def on_timeout(self):
        if not self.cashed_out and not self.crashed:
            self.running = False
            self.services.users.credit_wallet(self.user_id, self.bet_amount)       # refund
            embed = discord.Embed(
                title="🚀 Crash - TIMEOUT",
                description=f"⏰ **Game timed out!**\n💰 **Refunded:** {CURRENCY}{self.bet_amount}",
                color=0x979C9F,
            )
            await self.interaction.edit_original_response(embed=embed, view=None)
            self.services.sessions.end(self.user_id)

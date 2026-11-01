"""Crash: the multiplier climbs every 2 seconds and may crash; cash out before it does."""
from __future__ import annotations

import asyncio
import logging
import random

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.ui.theme import CRASHICON, CURRENCY, Colors
from rudhabot.views.base import ServiceView

log = logging.getLogger("rudhabot.crash")


def _authored(embed: discord.Embed, user) -> discord.Embed:
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


def opening_embed(bet_amount: int, user) -> discord.Embed:
    return _authored(discord.Embed(
        title=f"{CRASHICON} Crash",
        description=f"Bet: {CURRENCY}{bet_amount}\n\nPress **Cash Out** before the multiplier crashes!",
        color=Colors.GAMBLING,
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
                title=f"{CRASHICON} Crash",
                description=f"Multiplier: x{self.multiplier:.2f}\n\n🎉 You cashed out and won {CURRENCY}{winnings}",
                color=Colors.SUCCESS,
            )
            self.services.sessions.end(self.user_id)      # before any Discord call: a failed edit must not lock them out
            await i.response.edit_message(embed=_authored(embed, i.user), view=None)

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
                            title=f"{CRASHICON} Crash",
                            description=f"Multiplier crashed at x{self.multiplier:.2f}!\n\n💸 You lose {CURRENCY}{self.bet_amount}",
                            color=Colors.LOSS,
                        )
                        self.services.sessions.end(self.user_id)
                        self.stop()
                        try:
                            await self.interaction.edit_original_response(embed=_authored(embed, self.interaction.user), view=None)
                        except discord.HTTPException:
                            pass                                 # the result stands even if the message is gone
                        break
                    embed = discord.Embed(
                        title=f"{CRASHICON} Crash",
                        description=f"Bet: {CURRENCY}{self.bet_amount}\nMultiplier: x{self.multiplier:.2f}\n\n"
                                    f"Press **Cash Out** before the multiplier crashes!",
                        color=Colors.GAMBLING,
                    )
                    await self.interaction.edit_original_response(embed=_authored(embed, self.interaction.user), view=self)
        except Exception:
            # e.g. the message was deleted: refund rather than leave the bet (and the player lock) hanging
            log.exception("crash loop failed for user %s", self.user_id)
            async with self.lock:
                if not self.cashed_out and not self.crashed:
                    self.running = False
                    self.crashed = True                          # settled: on_timeout must not refund again
                    self.services.users.credit_wallet(self.user_id, self.bet_amount)
                    self.services.sessions.end(self.user_id)
                    self.stop()

    async def on_timeout(self):
        if not self.cashed_out and not self.crashed:
            self.running = False
            self.services.users.credit_wallet(self.user_id, self.bet_amount)       # refund
            self.services.sessions.end(self.user_id)
            embed = discord.Embed(
                title=f"{CRASHICON} Crash - TIMEOUT",
                description=f"⏰ **Game timed out!**\n💰 **Refunded:** {CURRENCY}{self.bet_amount}",
                color=Colors.COOLDOWN,
            )
            try:
                await self.interaction.edit_original_response(embed=embed, view=None)
            except discord.HTTPException:
                pass

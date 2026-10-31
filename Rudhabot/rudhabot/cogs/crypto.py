"""Cryptocurrency commands and the per-minute price update job."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import tasks

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import not_in_game
from rudhabot.services.crypto_market import TradeRefused
from rudhabot.ui.replies import respond
from rudhabot.ui.theme import CURRENCY

log = logging.getLogger("rudhabot.crypto")

COIN_CHOICES = [app_commands.Choice(name=symbol, value=symbol) for symbol in ("SCAY", "RDC", "LICH", "WNC")]


def _embed(user, message: str, color: int = 0x992D22) -> discord.Embed:
    embed = discord.Embed(description=message, color=color)
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


class CryptoCog(ServiceCog):
    async def cog_load(self):
        self.update_prices.start()

    async def cog_unload(self):
        self.update_prices.cancel()

    @tasks.loop(minutes=1)
    async def update_prices(self):
        try:
            self.services.crypto.tick()
        except Exception:
            log.exception("price update failed")

    @update_prices.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="crypto", description="Check current cryptocurrency market.")
    @not_in_game()
    async def crypto(self, interaction: discord.Interaction):
        market = self.services.crypto
        account = self.users.get(interaction.user.id)
        embed = discord.Embed(
            title="<:crypto:1401378589491859598> Crypto Market",
            description=f"Here, you can buy available cryptocurrencies legalized by the government. Prices change every minute. \n"
                        f"You have {CURRENCY}{account.get('wallet', 0)} in your wallet.",
            color=0xFEE75C,
        )
        embed.set_footer(text="Use /buycoin to buy cryptocurrencies")
        for coin, price in market.prices.items():
            embed.add_field(name=f"{market.coins[coin].name} ({coin})", value=f"{CURRENCY}{round(int(price), 2)}", inline=False)
        await respond(interaction, embed)

    @app_commands.command(name="buycoin", description="Buy a cryptocurrency.")
    @app_commands.describe(coin="SCAY, RDC, LICH, or WNC", amount="Amount in your wallet to spend")
    @app_commands.choices(coin=COIN_CHOICES)
    @not_in_game()
    async def buycoin(self, interaction: discord.Interaction, coin: str, amount: int):
        try:
            trade = self.services.crypto.buy(interaction.user.id, coin, amount)
        except TradeRefused as refusal:
            await respond(interaction, _embed(interaction.user, {
                "invalid_coin": "❌ Invalid coin symbol.",
                "not_positive": "❌ Amount must be positive.",
                "insufficient_wallet": "❌ You don't have enough money in your wallet.",
            }[refusal.code]), ephemeral=True)
            return
        await respond(interaction, _embed(interaction.user, f"✅ You bought {trade.quantity:.4f} {trade.coin} for {CURRENCY}{amount}.", 0x57F287))

    @app_commands.command(name="sellcoin", description="Sell a cryptocurrency.")
    @app_commands.describe(coin="SCAY, RDC, LICH, or WNC", amount="Amount of the coin to sell or 'all'")
    @app_commands.choices(coin=COIN_CHOICES)
    @not_in_game()
    async def sellcoin(self, interaction: discord.Interaction, coin: str, amount: str):
        try:
            trade = self.services.crypto.sell(interaction.user.id, coin, amount)
        except TradeRefused as refusal:
            await respond(interaction, _embed(interaction.user, {
                "invalid_coin": "❌ Invalid coin symbol.",
                "invalid_amount": "❌ Invalid amount format. Use a number or 'all'.",
                "not_positive": "❌ Amount must be positive.",
                "insufficient_coins": f"❌ You don't have enough {refusal.data.get('coin')} to sell.",
            }[refusal.code]), ephemeral=True)
            return
        await respond(interaction, _embed(interaction.user, f"✅ You sold {trade.quantity:.4f} {trade.coin} for {CURRENCY}{trade.value}.", 0x57F287))

    @app_commands.command(name="portfolio", description="Check your cryptocurrency portfolio.")
    @not_in_game()
    async def portfolio(self, interaction: discord.Interaction):
        holdings = self.services.crypto.portfolio(self.users.get(interaction.user.id))
        embed = discord.Embed(title=f"📊 {interaction.user.name}'s Portfolio", color=0x11806A)
        if not holdings:
            embed.description = "Your portfolio is empty. You have no holdings."
        else:
            total_value = 0
            for holding in holdings:
                total_value += holding.value
                embed.add_field(name=f"{holding.coin} ({holding.name})",
                                value=f"You own {holding.quantity:.4f}, Worth {CURRENCY}{holding.value}", inline=False)
            embed.add_field(name="Total Crypto Value", value=f"{CURRENCY}{round(total_value, 2)}", inline=False)
            embed.set_footer(text="Use /sellcoin to sell your holdings")
        await respond(interaction, embed)

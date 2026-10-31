"""/stocks: the Rudharta Exchange (NAS, JOOT). Prices follow what the whole server buys at the shop and the café."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import not_in_game
from rudhabot.domain.stocks import LISTINGS
from rudhabot.services.stocks import TradeRefused
from rudhabot.ui.replies import respond
from rudhabot.ui.theme import CURRENCY

STOCKS = "<:stocks:1401378607368241273>"
SYMBOLS = [app_commands.Choice(name=f"{l.symbol} · {l.name}", value=l.symbol) for l in LISTINGS.values()]
SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 24) -> str:
    if len(values) < 2:
        return ""
    step = max(1, len(values) // width)
    sample = values[::step][-width:]
    lo, hi = min(sample), max(sample)
    span = (hi - lo) or 1
    return "".join(SPARK[min(7, int((v - lo) / span * 7.999))] for v in sample)


def _quote_line(q: dict) -> str:
    arrow = "🟢" if q["change_24h"] >= 0 else "🔴"
    return (f"{arrow} **{q['price']:,.2f}** ({q['change_24h'] * 100:+.1f}% 24h)\n`{sparkline(q['history'])}`\n"
            f"Revenue 24h {CURRENCY}{q['revenue_24h']:,.0f} · fair value ≈ {q['value']:,.2f}")


class StocksCog(ServiceCog, commands.GroupCog, group_name="stocks", group_description="The Rudharta stock exchange"):
    @property
    def exchange(self):
        return self.services.stocks

    async def _refuse(self, interaction, error: TradeRefused) -> None:
        await respond(interaction, discord.Embed(description=str(error), color=0x992D22), ephemeral=True)

    @app_commands.command(name="market", description="Prices of every listed company")
    async def market(self, interaction: discord.Interaction):
        e = discord.Embed(title=f"{STOCKS} Rudharta Exchange", color=0x2ECC71,
                          description="Prices follow what the whole server buys at the shop (NAS) and the café (JOOT), "
                                      "plus normal market noise. Each person's purchases count fully up to 2,000 a day.")
        for symbol in LISTINGS:
            q = self.exchange.quote(symbol)
            e.add_field(name=f"{q['symbol']} · {q['name']}", value=_quote_line(q), inline=False)
        e.set_footer(text="Trades move the price and pay a 0.5% fee · /stocks buy · /stocks sell · /stocks portfolio")
        await respond(interaction, e)

    @app_commands.command(name="quote", description="Price, trend and fundamentals for one company")
    @app_commands.choices(symbol=SYMBOLS)
    async def quote(self, interaction: discord.Interaction, symbol: app_commands.Choice[str]):
        q = self.exchange.quote(symbol.value)
        e = discord.Embed(title=f"{STOCKS} {q['symbol']} · {q['name']}", description=_quote_line(q), color=0x2ECC71)
        e.add_field(name="Market cap", value=f"{CURRENCY}{q['market_cap']:,.0f}")
        e.add_field(name="Revenue rate", value=f"{CURRENCY}{q['revenue_rate']:,.0f}/day")
        await respond(interaction, e)

    @app_commands.command(name="buy", description="Buy shares (paid from your wallet)")
    @app_commands.choices(symbol=SYMBOLS)
    @not_in_game()
    async def buy(self, interaction: discord.Interaction, symbol: app_commands.Choice[str], shares: app_commands.Range[int, 1]):
        try:
            t = self.exchange.buy(interaction.user.id, symbol.value, shares)
        except TradeRefused as error:
            return await self._refuse(interaction, error)
        e = discord.Embed(title=f"{STOCKS} Bought {t.shares:,} {t.symbol}", color=0x57F287,
                          description=f"Paid {CURRENCY}{t.total:,} (avg {t.gross / t.shares:,.2f} + fee {t.fee:,}).\n"
                                      f"Price moved {t.price_before:,.2f} → {t.price_after:,.2f}.")
        await respond(interaction, e)

    @app_commands.command(name="sell", description="Sell shares (paid to your wallet)")
    @app_commands.choices(symbol=SYMBOLS)
    @app_commands.describe(shares="How many shares (leave empty to sell all)")
    @not_in_game()
    async def sell(self, interaction: discord.Interaction, symbol: app_commands.Choice[str],
                   shares: Optional[app_commands.Range[int, 1]] = None):
        try:
            t = self.exchange.sell(interaction.user.id, symbol.value, shares)
        except TradeRefused as error:
            return await self._refuse(interaction, error)
        e = discord.Embed(title=f"{STOCKS} Sold {t.shares:,} {t.symbol}", color=0x57F287,
                          description=f"Received {CURRENCY}{t.total:,} (avg {t.gross / t.shares:,.2f} − fee {t.fee:,}).\n"
                                      f"Price moved {t.price_before:,.2f} → {t.price_after:,.2f}.")
        await respond(interaction, e)

    @app_commands.command(name="portfolio", description="Your shares, their value and profit/loss")
    async def portfolio(self, interaction: discord.Interaction):
        rows = self.exchange.portfolio(interaction.user.id)
        e = discord.Embed(title=f"{STOCKS} {interaction.user.display_name}'s portfolio", color=0x2ECC71)
        for r in rows:
            e.add_field(name=f"{r['symbol']} × {r['shares']:,}", inline=False,
                        value=f"Value {CURRENCY}{r['value']:,.0f} · cost {CURRENCY}{r['cost']:,.0f} · "
                              f"{'🟢' if r['pnl'] >= 0 else '🔴'} {r['pnl']:+,.0f}")
        if not rows:
            e.description = "No shares yet. See `/stocks market`."
        await respond(interaction, e)

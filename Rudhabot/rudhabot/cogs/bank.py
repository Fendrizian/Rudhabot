"""/bank: card tiers and credit cards (balances, with interest, are in /balance); pays daily bank interest.
Thin: parse input, call BankService, render."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import not_in_game
from rudhabot.domain.bank import TIERS, next_tier
from rudhabot.services.bank import BankRefused
from rudhabot.ui.replies import respond
from rudhabot.ui.theme import BANKICON, CLAIRVOYANCEICON, CURRENCY, Colors

BANK_GREEN = Colors.BANK


def _amount(text: Optional[str]) -> Optional[int]:
    """None = 'all'. Raises BankRefused on garbage."""
    if text is None or text.strip().lower() == "all":
        return None
    try:
        value = int(text.replace(",", "").strip())
    except ValueError:
        raise BankRefused("❌ Use a whole number or 'all'.") from None
    if value <= 0:
        raise BankRefused("❌ Amount must be positive.")
    return value


def _embed(user, title: str, description: str = "", color: int = BANK_GREEN) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


class BankCog(ServiceCog, commands.GroupCog, group_name="bank", group_description="Savings, card tiers and limits"):
    card = app_commands.Group(name="card", description="Your Rudharta credit card")

    @property
    def bank(self):
        return self.services.bank

    async def cog_load(self) -> None:
        self.interest.start()

    async def cog_unload(self) -> None:
        self.interest.cancel()

    @tasks.loop(hours=1)
    async def interest(self) -> None:
        """Daily interest on bank balances (each account is credited at most once a day)."""
        try:
            self.bank.apply_daily_interest()
        except Exception:
            import logging
            logging.getLogger("rudhabot.bank").exception("daily interest failed")

    def _insights(self, user_id, profile: dict, bank_balance: int) -> list[str]:
        from rudhabot.services.insights import insights_for
        return insights_for(self.services, user_id, profile, bank_balance)

    async def _refuse(self, interaction, error: BankRefused) -> None:
        await respond(interaction, _embed(interaction.user, f"{BANKICON} Bank", str(error), Colors.FAIL), ephemeral=True)

    @app_commands.command(name="tiers", description="Compare the card tiers")
    async def tiers(self, interaction: discord.Interaction):
        e = discord.Embed(title="💳 Rudharta Bank card tiers", color=BANK_GREEN)
        for t in TIERS:
            e.add_field(name=f"{t.emoji} {t.name}", inline=False,
                        value=f"Bank interest **{t.daily_interest * 100:.2f}%/day** (on up to {t.interest_cap:,}) · "
                              f"deposit & withdraw limit "
                              f"**{t.monthly_limit:,}/month** each · credit limit {t.credit_limit:,}"
                              + (f" · upgrade: net worth {t.net_worth_required:,}, fee {t.upgrade_fee:,}" if t.upgrade_fee else " · everyone starts here"))
        await respond(interaction, e)

    @app_commands.command(name="upgrade", description="Upgrade your card to the next tier")
    @not_in_game()
    async def upgrade(self, interaction: discord.Interaction):
        try:
            tier = self.bank.upgrade(interaction.user.id)
        except BankRefused as error:
            return await self._refuse(interaction, error)
        await respond(interaction, _embed(interaction.user, "🎉 Card upgraded", f"Welcome to {tier.emoji} **{tier.name}**!",
                                          Colors.SUCCESS))

    @app_commands.command(name="history", description="Your recent money movements (from the audit log)")
    async def history(self, interaction: discord.Interaction):
        entries = self.services.audit.recent(15, interaction.user.id)
        lines = [f"`{e['ts'][5:16].replace('T', ' ')}` {e['delta']:+,} {e['account']} · {e['reason']}" for e in reversed(entries)]
        await respond(interaction, _embed(interaction.user, "🧾 Recent transactions", "\n".join(lines)[:4000] or "Nothing yet."),
                      ephemeral=True)

    # ------------------------------------------------------------------ credit card
    @card.command(name="info", description="Your credit card balance, limit and statement")
    async def card_info(self, interaction: discord.Interaction):
        profile = self.bank.account(interaction.user.id)
        tier, credit = self.bank.tier_of(profile), profile["credit"]
        e = _embed(interaction.user, f"{tier.emoji} {tier.name} credit card")
        e.add_field(name="Owed", value=f"{CURRENCY}{credit['balance']:,}")
        e.add_field(name="Available", value=f"{CURRENCY}{self.bank.credit_available(profile):,} of {tier.credit_limit:,}")
        e.add_field(name="Statement", value=(f"{CURRENCY}{credit['statement']:,} due {credit['due']}" if credit.get("statement")
                                             else "Nothing due"), inline=False)
        if credit.get("frozen"):
            e.add_field(name="❄️ Frozen", value="Pay the statement in full to unfreeze (`/bank card pay`).", inline=False)
        tips = [t for t in self._insights(interaction.user.id, profile, self.users.get(interaction.user.id)["bank"])
                if "card" in t]
        if tips:
            e.add_field(name=f"{CLAIRVOYANCEICON} Clairvoyance", value="\n".join(tips), inline=False)
        e.set_footer(text="Pay with /buy pay_with:card or /tcg wish shards pay_with:card · statements close on the 1st, due the 11th, "
                          "5% interest if late")
        await respond(interaction, e, ephemeral=True)

    @card.command(name="pay", description="Pay off your credit card")
    @app_commands.describe(amount="Amount or 'all'", source="Pay from your wallet or your bank balance")
    @app_commands.choices(source=[app_commands.Choice(name="Wallet", value="wallet"), app_commands.Choice(name="Bank", value="bank")])
    @not_in_game()
    async def card_pay(self, interaction: discord.Interaction, amount: str = "all",
                       source: Optional[app_commands.Choice[str]] = None):
        try:
            paid = self.bank.pay_card(interaction.user.id, _amount(amount), source.value if source else "wallet")
        except BankRefused as error:
            return await self._refuse(interaction, error)
        left = self.bank.account(interaction.user.id)["credit"]["balance"]
        await respond(interaction, _embed(interaction.user, "💳 Card payment", f"Paid {CURRENCY}{paid:,}. Still owed: {CURRENCY}{left:,}.",
                                          Colors.SUCCESS))

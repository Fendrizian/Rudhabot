"""Core economy commands: balance, earning, banking, robbing, sending money, leaderboard."""
from __future__ import annotations

import math
from datetime import timezone

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import not_in_game
from rudhabot.domain.amounts import AmountError, parse_amount
from rudhabot.domain.cooldown import CRIME, DAILY, ROB, WORK, Cooldown, format_remaining
from rudhabot.ui.replies import respond
from rudhabot.ui.theme import CURRENCY
from rudhabot.views.leaderboard import LeaderboardView, create_leaderboard_embed

INVALID_AMOUNT = "❌ Invalid amount format. Use a number or 'all'."
NOT_POSITIVE = "❌ Amount must be positive."


def _plain(user, message: str, color: int = 0x992D22) -> discord.Embed:
    embed = discord.Embed(description=message, color=color)
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


class EconomyCog(ServiceCog):
    def __init__(self, bot):
        super().__init__(bot)
        self.rng = self.services.rng

    def _now_utc(self):
        """Naive UTC 'now', the form cooldown stamps are stored in."""
        return self.clock.now(timezone.utc).replace(tzinfo=None)

    # ------------------------------------------------------------------ balance
    @app_commands.command(name="balance", description="Check your wallet and bank balance")
    @app_commands.describe(user="User to check balance for")
    @not_in_game()
    async def balance(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        account = self.users.get(target.id)
        embed = discord.Embed(title=f"<:balance:1399370324734246952> {target.name}'s Balance", color=0xAD1457)
        embed.add_field(name="Wallet", value=f"{CURRENCY}{account.get('wallet', 0)}", inline=False)
        embed.add_field(name="Bank", value=f"{CURRENCY}{account.get('bank', 0)}", inline=False)
        embed.add_field(name="Total Balance", value=f"{CURRENCY}{account.get('wallet', 0) + account.get('bank', 0)}", inline=False)
        await respond(interaction, embed)

    # ------------------------------------------------------------------ earning
    async def _on_cooldown(self, interaction, cooldown: Cooldown, account: dict, now, text: str, **fmt) -> bool:
        remaining = cooldown.remaining(account, now)
        if remaining is None:
            return False
        await respond(interaction, _plain(interaction.user, f"⏳ You need to wait **{format_remaining(remaining, **fmt)}** {text}", 0x979C9F))
        return True

    @app_commands.command(name="work", description="Work and earn some money!")
    @not_in_game()
    async def work(self, interaction: discord.Interaction):
        account, now = self.users.get(interaction.user.id), self._now_utc()
        if await self._on_cooldown(interaction, WORK, account, now, "before working again."):
            return
        earnings = self.rng.randint(10, 30)
        self.users.update(interaction.user.id, wallet=account["wallet"] + earnings, last_work=Cooldown.stamp(now))
        await respond(interaction, _plain(interaction.user, f"💼 You worked hard and earned **{CURRENCY}{earnings}**!", 0xF1C40F))

    @app_commands.command(name="daily", description="Claim your daily reward of 50ra")
    @not_in_game()
    async def daily(self, interaction: discord.Interaction):
        account, now = self.users.get(interaction.user.id), self._now_utc()
        if await self._on_cooldown(interaction, DAILY, account, now, "before claiming daily again.", hours=True):
            return
        self.users.update(interaction.user.id, wallet=account["wallet"] + 50, last_daily=Cooldown.stamp(now))
        await respond(interaction, _plain(interaction.user, f"📅 You claimed your daily reward of **{CURRENCY}50**!", 0xF1C40F))

    @app_commands.command(name="crime", description="Commit a crime and try to earn money")
    @not_in_game()
    async def crime(self, interaction: discord.Interaction):
        account, now = self.users.get(interaction.user.id), self._now_utc()
        if await self._on_cooldown(interaction, CRIME, account, now, "before committing another crime."):
            return
        if self.rng.randint(1, 100) <= 70:
            earnings = self.rng.randint(30, 50)
            self.users.update(interaction.user.id, wallet=account["wallet"] + earnings, last_crime=Cooldown.stamp(now))
            await respond(interaction, _plain(interaction.user, f"🚬 You committed a crime and earned {CURRENCY}{earnings}", 0xF1C40F))
            return
        total_balance = account["wallet"] + account["bank"]
        fine = max(int(total_balance * self.rng.randint(5, 20) / 100), 1)
        if total_balance < 1:
            await respond(interaction, _plain(interaction.user, "You were caught while trying to commit a crime!\n"
                                              "You're too poor to even be fined, you broke bandit.", 0xED4245))
            return
        self.users.update(interaction.user.id, wallet=account["wallet"] - fine, last_crime=Cooldown.stamp(now))
        await respond(interaction, _plain(interaction.user, f"You got caught while trying to commit a crime!\nYou were fined {CURRENCY}{fine}", 0xED4245))

    @app_commands.command(name="rob", description="Try to rob another user!")
    @app_commands.describe(target="The user you want to rob")
    @not_in_game()
    async def rob(self, interaction: discord.Interaction, target: discord.User):
        if target.id == interaction.user.id:
            await respond(interaction, _plain(interaction.user, "❌ You can't rob yourself!"), ephemeral=True)
            return
        attacker, victim = self.users.get(interaction.user.id), self.users.get(target.id)
        now = self._now_utc()
        remaining = ROB.remaining(attacker, now)
        if remaining is not None:
            await respond(interaction, _plain(interaction.user, 
                f"⏳ You need to wait **{format_remaining(remaining, always_minutes=True)}** before you can rob someone again.", 0x979C9F))
            return
        if self.rng.choice([True, False]):
            if victim["wallet"] < 50:
                await respond(interaction, _plain(interaction.user, f"❌ {target.mention} doesn't have enough money for you to rob."))
                return
            stolen = max(1, int((self.rng.randint(50, 75) / 100) * victim["wallet"]))
            self.users.update(interaction.user.id, wallet=attacker["wallet"] + stolen, last_rob=Cooldown.stamp(now))
            self.users.update(target.id, wallet=victim["wallet"] - stolen)
            await respond(interaction, _plain(interaction.user, f"You successfully robbed {target.mention}!\n"
                                              f"You stole **{CURRENCY}{stolen}** from {target.mention}", 0x57F287))
            return
        total_balance = attacker["wallet"] + attacker.get("bank", 0)
        fine = max(1, int((self.rng.randint(10, 25) / 100) * total_balance))
        if total_balance < 1:
            await respond(interaction, _plain(interaction.user, f"You were caught trying to rob {target.mention}!\n"
                                              f"You're too poor to even be fined, you broke bandit.", 0xED4245))
            return
        self.users.update(interaction.user.id, wallet=attacker["wallet"] - fine, last_rob=Cooldown.stamp(now))
        await respond(interaction, _plain(interaction.user, f"You were caught trying to rob {target.mention}!\n"
                                          f"You were fined **{CURRENCY}{fine}**.", 0xED4245))

    # ------------------------------------------------------------------ banking
    async def _bank_transfer(self, interaction, amount_text: str, source: str, destination: str,
                             shortage: str, verb: str, color: int):
        account = self.users.get(interaction.user.id)
        try:
            amount = parse_amount(amount_text, account[source])
        except AmountError as error:
            await respond(interaction, _plain(interaction.user, INVALID_AMOUNT if error.code == "invalid" else NOT_POSITIVE), ephemeral=True)
            return
        if amount > account[source]:
            await respond(interaction, _plain(interaction.user, shortage))
            return
        self.users.update(interaction.user.id, **{source: account[source] - amount, destination: account[destination] + amount})
        await respond(interaction, _plain(interaction.user, verb.format(c=CURRENCY, n=amount), color))

    async def _bank_move(self, interaction: discord.Interaction, amount: str, kind: str, verb: str, color: int):
        """Deposits and withdrawals go through the bank, which enforces the card tier's monthly limits."""
        from rudhabot.services.bank import BankRefused
        text = amount.strip().lower()
        if text != "all":
            try:
                value = parse_amount(amount, 0)
            except AmountError:
                await respond(interaction, _plain(interaction.user, INVALID_AMOUNT))
                return
            if value <= 0:
                await respond(interaction, _plain(interaction.user, NOT_POSITIVE))
                return
        try:
            moved = self.services.bank.transfer(interaction.user.id, None if text == "all" else value, kind)
        except BankRefused as refusal:
            await respond(interaction, _plain(interaction.user, str(refusal)))
            return
        await respond(interaction, _plain(interaction.user, verb.format(c=CURRENCY, n=moved), color))

    @app_commands.command(name="deposit", description="Deposit money to your bank (monthly limit depends on your card tier)")
    @app_commands.describe(amount="Amount to deposit or 'all'")
    @not_in_game()
    async def deposit(self, interaction: discord.Interaction, amount: str):
        await self._bank_move(interaction, amount, "deposit", "🏦 You deposited **{c}{n}** to your bank.", 0x206694)

    @app_commands.command(name="withdraw", description="Withdraw money from your bank (monthly limit depends on your card tier)")
    @app_commands.describe(amount="Amount to withdraw or 'all'")
    @not_in_game()
    async def withdraw(self, interaction: discord.Interaction, amount: str):
        await self._bank_move(interaction, amount, "withdraw", "🏦 You withdrew **{c}{n}** to your wallet.", 0x3498DB)

    @app_commands.command(name="send", description="Give money from your wallet to another user")
    @app_commands.describe(target="The user to give money to", amount="Amount of money to give or 'all'")
    @not_in_game()
    async def send(self, interaction: discord.Interaction, target: discord.User, amount: str):
        if target.id == interaction.user.id:
            await respond(interaction, _plain(interaction.user, "❌ You cannot give money to yourself!"), ephemeral=True)
            return
        sender = self.users.get(interaction.user.id)
        try:
            give = parse_amount(amount, sender["wallet"])
        except AmountError as error:
            await respond(interaction, _plain(interaction.user, INVALID_AMOUNT if error.code == "invalid" else NOT_POSITIVE), ephemeral=True)
            return
        if give > sender["wallet"]:
            await respond(interaction, _plain(interaction.user, "❌ You don't have enough money in your wallet."), ephemeral=True)
            return
        receiver = self.users.get(target.id)
        self.users.update(interaction.user.id, wallet=sender["wallet"] - give)
        self.users.update(target.id, wallet=receiver["wallet"] + give)
        embed = _plain(interaction.user, f"💸 You gave {CURRENCY}{give} to {target.mention}!", 0x57F287)
        embed.add_field(name="Your New Balance", value=f"{CURRENCY}{sender['wallet'] - give}", inline=True)
        embed.add_field(name=f"{target.name}'s New Balance", value=f"{CURRENCY}{receiver['wallet'] + give}", inline=True)
        await respond(interaction, embed)

    # -------------------------------------------------------------- leaderboard
    @app_commands.command(name="leaderboard", description="Show the server leaderboard")
    @app_commands.describe(type="Leaderboard type: wallet, bank, total")
    @not_in_game()
    async def leaderboard(self, interaction: discord.Interaction, type: str = "total"):
        type = type.lower()
        if type not in ("wallet", "bank", "total"):
            type = "total"
        entries = []
        for user_id, account in self.users.all().items():
            wallet, bank = account.get("wallet", 0), account.get("bank", 0)
            entries.append((user_id, wallet if type == "wallet" else bank if type == "bank" else wallet + bank))
        entries.sort(key=lambda entry: entry[1], reverse=True)
        total_pages = max(1, math.ceil(len(entries) / 10))
        embed = create_leaderboard_embed(entries, 0, total_pages, type)
        view = LeaderboardView(entries, 0, total_pages, type)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)

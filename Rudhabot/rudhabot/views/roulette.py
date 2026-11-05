"""American roulette: the betting view, bet buttons and the bet-amount forms.

Bets are taken from the wallet when they are placed and paid when the wheel stops. The wheel spins once: the first
press of SPIN closes the table (a second press, or a bet form submitted after that, is refused and charges nothing),
so one stake can never be paid twice. A table left unspun for 5 minutes refunds its bets, which is fair because no
number has been drawn yet.
"""
from __future__ import annotations

import asyncio
import random

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.ui.messages import MSG_INVALIDBET, MSG_NONPOS
from rudhabot.ui.theme import CURRENCY, Colors, ROULETTE_BLACK, ROULETTE_GREEN, ROULETTE_RED
from rudhabot.views.base import ServiceButton, ServiceModal, ServiceView

WHEEL_ICON = "<a:roulette_wheel:1400109956761522206>"
NUMBERS = list(range(0, 37)) + ["00"]
RED = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})
BLACK = frozenset({2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35})
SPIN_SECONDS = 5

# (button label, bet type, payout to 1); a custom single number pays 35
BETS = [("Red", "red", 1), ("Black", "black", 1), ("Even", "even", 1), ("Odd", "odd", 1), ("1-18", "low", 1),
        ("19-36", "high", 1), ("1st Dozen", "first_dozen", 2), ("2nd Dozen", "second_dozen", 2),
        ("3rd Dozen", "third_dozen", 2), ("1st Column", "first_column", 2),
        ("2nd Column", "second_column", 2), ("3rd Column", "third_column", 2), ("0", "single_zero", 35),
        ("00", "double_zero", 35), ("0-00 Split", "zero_split", 17),
        ("Five Numbers", "five_numbers", 6)]


def wins(bet_type: str, number) -> bool:
    """Whether a bet of `bet_type` wins when the ball lands on `number` (0-36 or "00")."""
    n = number if isinstance(number, int) and number > 0 else None      # 0 and 00 lose every outside bet
    if bet_type.startswith("number_"):
        return number == int(bet_type.split("_")[1])
    return {
        "red": number in RED, "black": number in BLACK,
        "even": n is not None and n % 2 == 0, "odd": n is not None and n % 2 == 1,
        "low": n is not None and n <= 18, "high": n is not None and n >= 19,
        "first_dozen": n is not None and n <= 12, "second_dozen": n is not None and 13 <= n <= 24,
        "third_dozen": n is not None and n >= 25,
        "first_column": n is not None and n % 3 == 1, "second_column": n is not None and n % 3 == 2,
        "third_column": n is not None and n % 3 == 0,
        "single_zero": number == 0, "double_zero": number == "00", "zero_split": number in (0, "00"),
        "five_numbers": number in (0, "00", 1, 2, 3),
    }.get(bet_type, False)


def bet_name(bet_type: str) -> str:
    return bet_type.replace("_", " ").title()


class RouletteView(ServiceView):
    def __init__(self, user_id, services, rng=random):
        super().__init__(services, timeout=300)
        self.user_id = user_id
        self.rng = rng
        self.bets: list[tuple[str, int, int]] = []          # (bet type, payout to 1, amount)
        self.total_bet = 0
        self.original_interaction = None
        self.spinning = False                                # set by the first SPIN: the table is closed
        self.game_over = False
        self.services.sessions.start(user_id)
        for label, bet_type, payout in BETS:
            self.add_item(BetButton(label, bet_type, payout))
        self.add_item(CustomNumberButton())
        self.add_item(SpinButton())

    async def interaction_check(self, interaction: Interaction):
        return interaction.user.id == self.user_id

    @property
    def closed(self) -> bool:
        return self.spinning or self.game_over

    async def on_timeout(self):
        """Nobody spun for 5 minutes: refund the bets (no number was drawn) and release the player."""
        if self.closed:
            return
        self.game_over = True
        if self.total_bet > 0:
            wallet = self.services.users.credit_wallet(self.user_id, self.total_bet)
            embed = discord.Embed(
                title=f"{WHEEL_ICON} ROULETTE - TIMEOUT",
                description=f"⏰ **Game timed out!**\n\n💰 **Refunded:** {CURRENCY}{self.total_bet}\n\n"
                            "All your bets have been returned to your wallet.",
                color=Colors.COOLDOWN,
            )
            embed.set_footer(text=f"Final wallet: {wallet}ra")
            try:
                await self.original_interaction.edit_original_response(embed=embed, view=None)
            except (discord.HTTPException, AttributeError):
                pass
        self.services.sessions.end(self.user_id)

    def table_embed(self, user, wallet: int) -> discord.Embed:
        lines = [f"{bet_name(t)}: {CURRENCY}{amount}" for t, _, amount in self.bets]
        embed = discord.Embed(
            title=f"{WHEEL_ICON} ROULETTE",
            description=f"**Total Bet:** {CURRENCY}{self.total_bet}\n**Current Bets:**\n" + "\n".join(lines)
                        + "\n\nPlace more bets or **SPIN** to play!",
            color=Colors.GAMBLING,
        )
        embed.set_author(name=user.name, icon_url=user.display_avatar.url)
        embed.add_field(name="Wallet", value=f"{CURRENCY}{wallet}")
        return embed

    async def place_bet(self, interaction: Interaction, bet_type: str, payout: int, amount_text: str,
                        confirmation: str = "✅ Bet placed!") -> None:
        """Validate and take one bet (shared by both bet forms)."""
        fail = lambda text: interaction.response.send_message(embed=discord.Embed(description=text, color=Colors.FAIL),
                                                              ephemeral=True)
        if self.closed:
            return await fail("❌ The wheel has already been spun: this bet wasn't placed, and nothing was charged.")
        try:
            amount = int(amount_text)
        except ValueError:
            return await fail(MSG_INVALIDBET)
        if amount <= 0:
            return await fail(MSG_NONPOS)
        wallet = self.services.users.get(interaction.user.id)["wallet"]
        if amount > wallet:
            return await fail(f"❌ You don't have enough money! You have {CURRENCY}{wallet} but need {CURRENCY}{amount}.")
        wallet = self.services.users.credit_wallet(interaction.user.id, -amount)
        self.bets.append((bet_type, payout, amount))
        self.total_bet += amount
        await interaction.response.send_message(content=confirmation, ephemeral=True)
        try:
            await self.original_interaction.edit_original_response(embed=self.table_embed(interaction.user, wallet),
                                                                   view=self)
        except discord.HTTPException:
            pass                                             # the bet stands; the table message just isn't updated


class BetButton(ServiceButton):
    def __init__(self, label, bet_type, payout):
        style = {"red": ButtonStyle.danger, "black": ButtonStyle.secondary}.get(bet_type, ButtonStyle.primary)
        super().__init__(label=label, style=style)
        self.bet_type = bet_type
        self.payout = payout

    async def callback(self, interaction: Interaction):
        await interaction.response.send_modal(BetAmountModal(self.bet_type, self.payout, self.label, self.view))


class BetAmountModal(ServiceModal):
    def __init__(self, bet_type, payout, label, roulette_view):
        super().__init__(roulette_view.services, title="Place Bet", audit_label="Roulette")
        self.bet_type, self.payout, self.roulette_view = bet_type, payout, roulette_view
        self.amount_input = ui.TextInput(label=f"Bet amount for {label}", placeholder="Enter amount to bet", required=True)
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: Interaction):
        await self.roulette_view.place_bet(interaction, self.bet_type, self.payout, self.amount_input.value)


class CustomNumberButton(ServiceButton):
    def __init__(self):
        super().__init__(label="Custom Number", style=ButtonStyle.primary, emoji="🔢")

    async def callback(self, interaction: Interaction):
        await interaction.response.send_modal(CustomNumberBetModal(self.view))


class CustomNumberBetModal(ServiceModal):
    def __init__(self, roulette_view):
        super().__init__(roulette_view.services, title="Bet on Custom Number", audit_label="Roulette")
        self.roulette_view = roulette_view
        self.number = ui.TextInput(label="Number (0-36)", placeholder="Enter a number from 0 to 36", required=True)
        self.amount = ui.TextInput(label="Bet Amount", placeholder="Enter amount to bet", required=True)
        self.add_item(self.number)
        self.add_item(self.amount)

    async def on_submit(self, interaction: Interaction):
        try:
            num = int(self.number.value)
            if not 0 <= num <= 36:
                raise ValueError("out of range")
        except ValueError:
            embed = discord.Embed(description="❌ Please enter a valid number between 0 and 36.", color=Colors.FAIL)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await self.roulette_view.place_bet(interaction, f"number_{num}", 35, self.amount.value,
                                           f"✅ Bet placed on Number {num}!")


class SpinButton(ServiceButton):
    def __init__(self):
        super().__init__(label="💫 SPIN", style=ButtonStyle.success, row=4)

    async def callback(self, interaction: Interaction):
        view: RouletteView = self.view
        if view.closed:                                      # a second press while the ball is already rolling
            return await interaction.response.send_message(content="The wheel is already spinning!", ephemeral=True)
        if not view.bets:
            embed = discord.Embed(description="❌ You must place at least one bet before spinning!", color=Colors.FAIL)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        view.spinning = True                                 # before any await: the table is closed from here on
        for item in view.children:
            item.disabled = True
        spinning = discord.Embed(title=f"{WHEEL_ICON} ROULETTE", description="The ball is spinning around the wheel...",
                                 color=Colors.GAMBLING)
        spinning.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        try:
            await interaction.response.edit_message(embed=spinning, view=view)
        except discord.HTTPException:
            pass                                             # the spin happens anyway: the bets must be settled
        await asyncio.sleep(SPIN_SECONDS)
        result = settle(view, interaction.user)
        try:
            await interaction.edit_original_response(embed=result, view=None)
        except discord.HTTPException:
            pass                                             # paid and released even if the message is gone


def settle(view: RouletteView, user) -> discord.Embed:
    """Draw the number, pay the winning bets, release the player and describe the result (no Discord calls)."""
    number = view.rng.choice(NUMBERS)
    total_winnings, winning = 0, []
    for bet_type, payout, amount in view.bets:
        if wins(bet_type, number):
            total_winnings += amount * (payout + 1)          # the stake back plus the payout
            winning.append(f"{bet_name(bet_type)} ({CURRENCY}{amount})")
    wallet = view.services.users.credit_wallet(user.id, total_winnings) if total_winnings else \
        view.services.users.get(user.id)["wallet"]
    view.game_over = True
    view.services.sessions.end(view.user_id)                 # before the Discord call: a failed edit must not lock them out
    view.stop()
    if number in RED:
        emoji, colour_name, colour = "🔴", "Red", ROULETTE_RED
    elif number in BLACK:
        emoji, colour_name, colour = "⚫", "Black", ROULETTE_BLACK
    else:
        emoji, colour_name, colour = "🟢", "Green", ROULETTE_GREEN
    embed = discord.Embed(title=f"{WHEEL_ICON} ROULETTE RESULT",
                          description=f"**Winning Number:** {emoji} **{number}** ({colour_name})", color=colour)
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    if winning:
        embed.add_field(name="🎉 Winning Bets", value="\n".join(winning), inline=False)
        embed.add_field(name="💰 Total Winnings", value=f"{CURRENCY}{total_winnings}", inline=True)
    embed.add_field(name="💵 Total Bet", value=f"{CURRENCY}{view.total_bet}", inline=True)
    net = total_winnings - view.total_bet
    if net > 0:
        embed.add_field(name="⏫️ Net Profit", value=f"{CURRENCY}{net}", inline=True)
        embed.color = Colors.SUCCESS
    elif net < 0:
        embed.add_field(name="⏬️ Net Loss", value=f"{CURRENCY}{abs(net)}", inline=True)
        embed.color = Colors.LOSS
    else:
        embed.add_field(name="⏺️ Break Even", value=f"{CURRENCY}0", inline=True)
        embed.color = Colors.GAMBLING
    embed.set_footer(text=f"Final wallet: {wallet}ra")
    return embed

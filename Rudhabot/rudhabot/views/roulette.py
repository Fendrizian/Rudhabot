"""American roulette: the betting view, bet buttons and the bet-amount modals."""
from __future__ import annotations

import asyncio
import random

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.ui.messages import MSG_INVALIDBET, MSG_NONPOS
from rudhabot.ui.theme import CURRENCY
from rudhabot.views.base import ServiceButton, ServiceModal, ServiceView


class RouletteView(ServiceView):
    def __init__(self, user_id, services):
        super().__init__(services, timeout=300)
        self.user_id = user_id
        self.bets = []
        self.total_bet = 0
        self.original_interaction = None
        self.game_over = False
        self.services.sessions.start(user_id)

        # Betting buttons
        self.add_item(BetButton("Red", "red", 1))
        self.add_item(BetButton("Black", "black", 1))
        self.add_item(BetButton("Even", "even", 1))
        self.add_item(BetButton("Odd", "odd", 1))
        self.add_item(BetButton("1-18", "low", 1))
        
        self.add_item(BetButton("19-36", "high", 1))
        self.add_item(BetButton("1st Dozen", "first_dozen", 2))
        self.add_item(BetButton("2nd Dozen", "second_dozen", 2))
        self.add_item(BetButton("3rd Dozen", "third_dozen", 2))
        self.add_item(BetButton("1st Column", "first_column", 2))
        
        self.add_item(BetButton("2nd Column", "second_column", 2))
        self.add_item(BetButton("3rd Column", "third_column", 2))
        self.add_item(BetButton("0", "single_zero", 35))
        self.add_item(BetButton("00", "double_zero", 35))
        self.add_item(BetButton("0-00 Split", "zero_split", 17))
        
        self.add_item(BetButton("Five Numbers", "five_numbers", 6))
        self.add_item(CustomNumberButton())
        self.add_item(SpinButton())

    async def interaction_check(self, interaction: Interaction):
        return interaction.user.id == self.user_id
    
    async def on_timeout(self):
        """Handle timeout by refunding bets and removing user from active gambling"""
        if self.game_over:
            return
        if self.total_bet > 0:
            # Refund all bets
            user_data = self.services.users.get(self.user_id)
            user_data["wallet"] += self.total_bet
            self.services.users.update(self.user_id, wallet=user_data["wallet"])
            
            # Update the embed to show timeout and refund
            timeout_embed = discord.Embed(
                title="<a:roulette_wheel:1400109956761522206> ROULETTE - TIMEOUT",
                description=f"⏰ **Game timed out!**\n\n💰 **Refunded:** {CURRENCY}{self.total_bet}\n\nAll your bets have been returned to your wallet.",
                color=0x979c9f
            )
            timeout_embed.set_footer(text=f"Final wallet: {user_data['wallet']}ra")
            
            try:
                await self.original_interaction.edit_original_response(embed=timeout_embed, view=None)
            except:
                pass
        
        self.services.sessions.end(self.user_id)

class BetButton(ServiceButton):
    def __init__(self, label, bet_type, payout):
        if bet_type == "red":
            style = ButtonStyle.danger
        elif bet_type == "black":
            style = ButtonStyle.secondary
        else:
            style = ButtonStyle.primary
            
        super().__init__(label=label, style=style)
        self.bet_type = bet_type
        self.payout = payout

    async def callback(self, interaction: Interaction):
        view: RouletteView = self.view
        await interaction.response.send_modal(BetAmountModal(self.bet_type, self.payout, self.label, view))

class BetAmountModal(ServiceModal):
    def __init__(self, bet_type, payout, bet_name, roulette_view):
        super().__init__(roulette_view.services, title="Place Bet")
        self.bet_type = bet_type
        self.payout = payout
        self.bet_name = bet_name
        self.roulette_view = roulette_view
        self.amount_input = ui.TextInput(
            label=f"Bet amount for {bet_name}",
            placeholder="Enter amount to bet",
            required=True
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: Interaction):
        try:
            bet_amount = int(self.amount_input.value)
        except ValueError:
            embed = discord.Embed(description=MSG_INVALIDBET, color=0x992d22)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if bet_amount <= 0:
            embed = discord.Embed(description=MSG_NONPOS, color=0x992d22)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Get current user wallet
        user_data = self.services.users.get(interaction.user.id)
        current_wallet = user_data["wallet"]

        if bet_amount > current_wallet:
            embed = discord.Embed(
                description=f"❌ You don't have enough money! You have {CURRENCY}{current_wallet} but need {CURRENCY}{bet_amount}.",
                color=0x992d22
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        view = self.roulette_view

        # Deduct money immediately
        user_data["wallet"] -= bet_amount
        self.services.users.update(interaction.user.id, wallet=user_data["wallet"])
        
        # Add bet to the view
        view.bets.append((self.bet_type, self.payout, bet_amount))
        view.total_bet += bet_amount

        # Update the embed
        bet_descriptions = []
        for bet_type, payout, amount in view.bets:
            bet_name = bet_type.replace("_", " ").title()
            bet_descriptions.append(f"{bet_name}: {CURRENCY}{amount}")

        embed = discord.Embed(
            title="<a:roulette_wheel:1400109956761522206> ROULETTE",
            description=f"**Total Bet:** {CURRENCY}{view.total_bet}\n**Current Bets:**\n" + "\n".join(bet_descriptions) + "\n\nPlace more bets or **SPIN** to play!",
            color=0x99aab5
        )
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Wallet", value=f"{CURRENCY}{user_data['wallet']}")
        
        await view.original_interaction.edit_original_response(embed=embed, view=view)
        await interaction.response.send_message("✅ Bet placed!", ephemeral=True)

class CustomNumberButton(ServiceButton):
    def __init__(self):
        super().__init__(label="Custom Number", style=ButtonStyle.primary, emoji="🔢")

    async def callback(self, interaction: Interaction):
        view: RouletteView = self.view
        await interaction.response.send_modal(CustomNumberBetModal(view))

class CustomNumberBetModal(ServiceModal):
    def __init__(self, roulette_view):
        super().__init__(roulette_view.services, title="Bet on Custom Number")
        self.roulette_view = roulette_view
        self.number = ui.TextInput(
            label="Number (0-36)",
            placeholder="Enter a number from 0 to 36",
            required=True
        )
        self.amount = ui.TextInput(
            label="Bet Amount",
            placeholder="Enter amount to bet",
            required=True
        )
        self.add_item(self.number)
        self.add_item(self.amount)

    async def on_submit(self, interaction: Interaction):
        try:
            num = int(self.number.value)
            if num < 0 or num > 36:
                raise ValueError("Invalid number")
        except ValueError:
            embed = discord.Embed(description="❌ Please enter a valid number between 0 and 36.", color=0x992d22)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            bet_amount = int(self.amount.value)
        except ValueError:
            embed = discord.Embed(description=MSG_INVALIDBET, color=0x992d22)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if bet_amount <= 0:
            embed = discord.Embed(description=MSG_NONPOS, color=0x992d22)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Get current user wallet
        user_data = self.services.users.get(interaction.user.id)
        current_wallet = user_data["wallet"]

        if bet_amount > current_wallet:
            embed = discord.Embed(
                description=f"❌ You don't have enough money! You have {CURRENCY}{current_wallet} but need {CURRENCY}{bet_amount}.",
                color=0x992d22
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        view = self.roulette_view

        # Deduct money immediately
        user_data["wallet"] -= bet_amount
        self.services.users.update(interaction.user.id, wallet=user_data["wallet"])
        
        # Add bet to the view
        bet_type = f"number_{num}"
        view.bets.append((bet_type, 35, bet_amount))
        view.total_bet += bet_amount

        # Update the embed
        bet_descriptions = []
        for bet_type, payout, amount in view.bets:
            bet_name = bet_type.replace("_", " ").title()
            bet_descriptions.append(f"{bet_name}: {CURRENCY}{amount}")

        embed = discord.Embed(
            title="<a:roulette_wheel:1400109956761522206> ROULETTE",
            description=f"**Total Bet:** {CURRENCY}{view.total_bet}\n**Current Bets:**\n" + "\n".join(bet_descriptions) + "\n\nPlace more bets or **SPIN** to play!",
            color=0x99aab5
        )
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Wallet", value=f"{CURRENCY}{user_data['wallet']}")
        
        await view.original_interaction.edit_original_response(embed=embed, view=view)
        await interaction.response.send_message(f"✅ Bet placed on Number {num}!", ephemeral=True)

class SpinButton(ServiceButton):
    def __init__(self):
        super().__init__(label="💫 SPIN", style=ButtonStyle.success, row=4)

    async def callback(self, interaction: Interaction):
        view: RouletteView = self.view
        
        if not view.bets:
            embed = discord.Embed(description="❌ You must place at least one bet before spinning!", color=0x992d22)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Disable all buttons
        for item in view.children:
            item.disabled = True
            
        spinning_embed = discord.Embed(
            title="<a:roulette_wheel:1400109956761522206> ROULETTE",
            description="The ball is spinning around the wheel...",
            color=0x99aab5
        )
        spinning_embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        
        await interaction.response.edit_message(embed=spinning_embed, view=view)
        await asyncio.sleep(5)

        # Generate winning number
        numbers = list(range(0, 37)) + ["00"]
        winning_number = random.choice(numbers)
        
        # Winning number properties
        red_numbers = [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]
        black_numbers = [2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35]
        
        # Calculate winnings
        total_winnings = 0
        winning_bets = []
        
        for bet_type, payout, bet_amount in view.bets:
            won = False
            
            if bet_type == "red" and winning_number in red_numbers:
                won = True
            elif bet_type == "black" and winning_number in black_numbers:
                won = True
            elif bet_type == "even" and isinstance(winning_number, int) and winning_number > 0 and winning_number % 2 == 0:
                won = True
            elif bet_type == "odd" and isinstance(winning_number, int) and winning_number > 0 and winning_number % 2 == 1:
                won = True
            elif bet_type == "low" and isinstance(winning_number, int) and 1 <= winning_number <= 18:
                won = True
            elif bet_type == "high" and isinstance(winning_number, int) and 19 <= winning_number <= 36:
                won = True
            elif bet_type == "first_dozen" and isinstance(winning_number, int) and 1 <= winning_number <= 12:
                won = True
            elif bet_type == "second_dozen" and isinstance(winning_number, int) and 13 <= winning_number <= 24:
                won = True
            elif bet_type == "third_dozen" and isinstance(winning_number, int) and 25 <= winning_number <= 36:
                won = True
            elif bet_type == "first_column" and isinstance(winning_number, int) and winning_number % 3 == 1 and winning_number > 0:
                won = True
            elif bet_type == "second_column" and isinstance(winning_number, int) and winning_number % 3 == 2 and winning_number > 0:
                won = True
            elif bet_type == "third_column" and isinstance(winning_number, int) and winning_number % 3 == 0 and winning_number > 0:
                won = True
            elif bet_type == "single_zero" and winning_number == 0:
                won = True
            elif bet_type == "double_zero" and winning_number == "00":
                won = True
            elif bet_type == "zero_split" and winning_number in [0, "00"]:
                won = True
            elif bet_type == "five_numbers" and winning_number in [0, "00", 1, 2, 3]:
                won = True
            elif bet_type.startswith("number_"):
                bet_number = int(bet_type.split("_")[1])
                if winning_number == bet_number:
                    won = True
            
            if won:
                # User gets their bet back plus the payout
                winnings = bet_amount + (bet_amount * payout)
                total_winnings += winnings
                bet_name = bet_type.replace("_", " ").title()
                winning_bets.append(f"{bet_name} ({CURRENCY}{bet_amount})")

        # Update user wallet with winnings
        user_data = self.services.users.get(interaction.user.id)
        user_data["wallet"] += total_winnings
        self.services.users.update(interaction.user.id, wallet=user_data["wallet"])

        net_result = total_winnings - view.total_bet

        # Result embed
        if winning_number in red_numbers:
            color_emoji = "🔴"
            color_name = "Red"
            embed_color = 0xdc143c
        elif winning_number in black_numbers:
            color_emoji = "⚫"
            color_name = "Black"
            embed_color = 0x2c2c2c
        else:
            color_emoji = "🟢"
            color_name = "Green"
            embed_color = 0x00ff00

        result_embed = discord.Embed(
            title="<a:roulette_wheel:1400109956761522206> ROULETTE RESULT",
            description=f"**Winning Number:** {color_emoji} **{winning_number}** ({color_name})",
            color=embed_color
        )
        result_embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        
        if winning_bets:
            result_embed.add_field(
                name="🎉 Winning Bets",
                value="\n".join(winning_bets),
                inline=False
            )
            result_embed.add_field(
                name="💰 Total Winnings",
                value=f"{CURRENCY}{total_winnings}",
                inline=True
            )
        
        result_embed.add_field(
            name="💵 Total Bet",
            value=f"{CURRENCY}{view.total_bet}",
            inline=True
        )
        
        if net_result > 0:
            result_embed.add_field(
                name="⏫️ Net Profit",
                value=f"{CURRENCY}{net_result}",
                inline=True
            )
            result_embed.color = 0x57f287
        elif net_result < 0:
            result_embed.add_field(
                name="⏬️ Net Loss",
                value=f"{CURRENCY}{abs(net_result)}",
                inline=True
            )
            result_embed.color = 0xed4245
        else:
            result_embed.add_field(
                name="⏺️ Break Even",
                value=f"{CURRENCY}0",
                inline=True
            )
            result_embed.color = 0x99aab5

        result_embed.set_footer(text=f"Final wallet: {user_data['wallet']}ra")

        try:
            self.services.sessions.end(view.user_id)
            view.game_over = True
            await interaction.edit_original_response(embed=result_embed, view=None)
        except Exception as e:
            self.services.sessions.end(view.user_id)
            view.game_over = True
            raise e

        view.game_over = True
        await interaction.edit_original_response(embed=result_embed, view=None)

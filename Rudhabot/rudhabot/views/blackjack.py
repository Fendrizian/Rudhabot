"""Blackjack's Discord view: buttons, wallet settlement and the game embed."""
from __future__ import annotations

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.domain.blackjack import HandOutcome, HandResult
from rudhabot.ui.theme import CURRENCY, Colors
from rudhabot.views.base import ServiceView

_RESULT_TEXT = {
    HandOutcome.BUST: "Bust - Lost {c}{n}",
    HandOutcome.BLACKJACK: "Blackjack! Won {c}{n}",
    HandOutcome.TWENTY_ONE: "21! Won {c}{n}",
    HandOutcome.DEALER_BUST: "Dealer Bust! Won {c}{n}",
    HandOutcome.WIN: "Won {c}{n}",
    HandOutcome.PUSH: "Push - Returned {c}{n}",
    HandOutcome.LOSS: "Lost {c}{n}",
}


def describe_result(result: HandResult) -> str:
    return _RESULT_TEXT[result.outcome].format(c=CURRENCY, n=result.amount)


class BlackjackView(ServiceView):
    def __init__(self, game, services):
        super().__init__(services, timeout=300)
        self.game = game
        self.update_buttons()
    
    def update_buttons(self):
        # Clear existing buttons
        self.clear_items()
        
        if not self.game.game_over:
            # Hit button
            hit_button = ui.Button(label="Hit", style=ButtonStyle.danger)
            hit_button.callback = self.hit_callback
            self.add_item(hit_button)
            
            # Stand button
            stand_button = ui.Button(label="Stand", style=ButtonStyle.success)
            stand_button.callback = self.stand_callback
            self.add_item(stand_button)
            
            # Double Down button
            if self.game.can_double_down():
                double_button = ui.Button(label="Double Down", style=ButtonStyle.primary)
                double_button.callback = self.double_callback
                self.add_item(double_button)
            
            # Split button
            if self.game.can_split():
                split_button = ui.Button(label="Split", style=ButtonStyle.secondary)
                split_button.callback = self.split_callback
                self.add_item(split_button)
    
    async def hit_callback(self, interaction: Interaction):
        if interaction.user.id != self.game.user_id:
            embed = discord.Embed(description="This isn't your game!", color=Colors.UNAVAILABLE)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        self.game.hit()
        self.update_buttons()
        embed = self.create_game_embed()
        
        # If game just ended, update wallet and remove from active players
        if self.game.game_over:
            total_winnings, _ = self.game.calculate_winnings()
            user_data = self.services.users.get(interaction.user.id)
            user_data["wallet"] += total_winnings
            self.services.users.update(interaction.user.id, wallet=user_data["wallet"])
            embed = self.create_game_embed()
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def stand_callback(self, interaction: Interaction):
        if interaction.user.id != self.game.user_id:
            embed = discord.Embed(description="This isn't your game!", color=Colors.UNAVAILABLE)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        self.game.stand()
        self.update_buttons()
        embed = self.create_game_embed()
        
        # If game just ended, update wallet and remove from active players
        if self.game.game_over:
            total_winnings, _ = self.game.calculate_winnings()
            user_data = self.services.users.get(interaction.user.id)
            user_data["wallet"] += total_winnings
            self.services.users.update(interaction.user.id, wallet=user_data["wallet"])
            embed = self.create_game_embed()
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def double_callback(self, interaction: Interaction):
        if interaction.user.id != self.game.user_id:
            embed = discord.Embed(description="This isn't your game!", color=Colors.UNAVAILABLE)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        user_data = self.services.users.get(interaction.user.id)
        if user_data["wallet"] < self.game.bet_amount:
            embed = discord.Embed(description=f"❌ You need {CURRENCY}{self.game.bet_amount} more to double down!", color=Colors.FAIL)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Deduct additional bet
        user_data["wallet"] -= self.game.bet_amount
        self.services.users.update(interaction.user.id, wallet=user_data["wallet"])
        
        self.game.double_down()
        self.update_buttons()
        embed = self.create_game_embed()
        
        # If game just ended, update wallet and remove from active players
        if self.game.game_over:
            total_winnings, _ = self.game.calculate_winnings()
            user_data = self.services.users.get(interaction.user.id)
            user_data["wallet"] += total_winnings
            self.services.users.update(interaction.user.id, wallet=user_data["wallet"])
            embed = self.create_game_embed()
        
        await interaction.response.edit_message(embed=embed, view=self)
        
    async def split_callback(self, interaction: Interaction):
        if interaction.user.id != self.game.user_id:
            embed = discord.Embed(description="This isn't your game!", color=Colors.UNAVAILABLE)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        user_data = self.services.users.get(interaction.user.id)
        if user_data["wallet"] < self.game.bet_amount:
            embed = discord.Embed(description=f"❌ You need {CURRENCY}{self.game.bet_amount} more to split!", color=Colors.FAIL)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Deduct additional bet
        user_data["wallet"] -= self.game.bet_amount
        self.services.users.update(interaction.user.id, wallet=user_data["wallet"])
        
        self.game.split()
        self.update_buttons()
        embed = self.create_game_embed()
        
        # If game just ended, update wallet and remove from active players
        if self.game.game_over:
            total_winnings, _ = self.game.calculate_winnings()
            user_data = self.services.users.get(interaction.user.id)
            user_data["wallet"] += total_winnings
            self.services.users.update(interaction.user.id, wallet=user_data["wallet"])
            embed = self.create_game_embed()
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    def create_game_embed(self):
        embed = discord.Embed(title="<:blackjack:1401208756041289758> BLACKJACK", color=Colors.GAMBLING)
        
        # Dealer's hand
        if self.game.game_over:
            dealer_display = self.game.dealer_hand.get_display()
            dealer_value = self.game.dealer_hand.get_display_value()
            embed.add_field(
                name=f"Dealer ({dealer_value})",
                value=dealer_display,
                inline=False
            )
        else:
            dealer_display = self.game.dealer_hand.get_display(hide_first=True)
            dealer_visible_value = self.game.dealer_hand.get_visible_value()
            embed.add_field(
                name=f"Dealer ({dealer_visible_value})",
                value=dealer_display,
                inline=False
            )
        
        # Player's hands
        for i, hand in enumerate(self.game.player_hands):
            hand_value = hand.get_display_value()
            hand_display = hand.get_display()
            
            # Hand indicator
            indicator = ""
            if not self.game.game_over and i == self.game.current_hand_index:
                indicator = " 👈"
            if self.game.doubled_down[i]:
                indicator += " (Doubled)"
            if hand.is_split_hand:
                indicator += " (Split)"
            
            status = ""
            if hand.is_blackjack():
                status = " - BLACKJACK!"
            elif hand.is_bust():
                status = " - BUST!"
            
            embed.add_field(
                name=f"Hand {i+1} ({hand_value}){status}{indicator}",
                value=hand_display,
                inline=False
            )
        
        # Game status
        if self.game.game_over:
            total_winnings, results = self.game.calculate_winnings()
            net_result = total_winnings - (self.game.bet_amount * len(self.game.player_hands))
            
            result_text = "\n".join([f"Hand {i+1}: {describe_result(result)}" for i, result in enumerate(results)])
            
            if net_result > 0:
                result_text += f"\n\n**⏫️ Net Income: {CURRENCY}{net_result}**"
                embed.color = Colors.SUCCESS
            elif net_result < 0:
                result_text += f"\n\n**⏬️ Net Loss: {CURRENCY}{abs(net_result)}**"
                embed.color = Colors.LOSS
            else:
                result_text += f"\n\n**⏺️ Net Result: {CURRENCY}0**"
                embed.color = Colors.GAMBLING
            
            embed.add_field(name="🃏 Results", value=result_text, inline=False)
        else:
            current_hand_num = self.game.current_hand_index + 1
            total_hands = len(self.game.player_hands)
            embed.add_field(
                name="▶️ Game Status", 
                value=f"Playing Hand {current_hand_num} of {total_hands}\nBet per hand: {CURRENCY}{self.game.bet_amount}",
                inline=False
            )
        
        return embed
    
    async def interaction_check(self, interaction: Interaction):
        return interaction.user.id == self.game.user_id
    
    async def on_timeout(self):
        """Handle timeout by refunding bet and ending the game"""
        if not self.game.game_over:
            # Calculate total bet amount (including doubled down and split bets)
            total_bet_amount = self.game.bet_amount * len(self.game.player_hands)
            for i, doubled in enumerate(self.game.doubled_down):
                if doubled:
                    total_bet_amount += self.game.bet_amount
            
            # Refund the bet
            user_data = self.services.users.get(self.game.user_id)
            user_data["wallet"] += total_bet_amount
            self.services.users.update(self.game.user_id, wallet=user_data["wallet"])
            
            # End the game
            self.game.end_game()
            
            # Create timeout embed
            timeout_embed = discord.Embed(
                title="<:blackjack:1401208756041289758> BLACKJACK - TIMEOUT",
                description=f"⏰ **Game timed out!**\n\n💰 **Refunded:** {CURRENCY}{total_bet_amount}\n\nYour bet has been returned to your wallet.",
                color=Colors.COOLDOWN
            )
            timeout_embed.set_footer(text=f"Final wallet: {user_data['wallet']}ra")
            
            try:
                # Try to edit the original message
                if hasattr(self, 'original_interaction'):
                    await self.original_interaction.edit_original_response(embed=timeout_embed, view=None)
            except discord.HTTPException:
                pass
        
        # Make sure user is removed from active gambling
        self.services.sessions.end(self.game.user_id)

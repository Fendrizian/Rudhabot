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
    """Hit / Stand / Double Down / Split. Each action changes the game, settles it once it is over, and redraws.

    Walking away doesn't refund the bet (that made every bad hand free): at timeout the remaining hands stand and the
    game is settled normally."""

    def __init__(self, game, services):
        super().__init__(services, timeout=300)
        self.game = game
        self.settled = False
        self.original_interaction = None          # set by the command: the timeout edits that message
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        if self.game.game_over:
            return
        actions = [("Hit", ButtonStyle.danger, self.hit_callback), ("Stand", ButtonStyle.success, self.stand_callback)]
        if self.game.can_double_down():
            actions.append(("Double Down", ButtonStyle.primary, self.double_callback))
        if self.game.can_split():
            actions.append(("Split", ButtonStyle.secondary, self.split_callback))
        for label, style, callback in actions:
            button = ui.Button(label=label, style=style)
            button.callback = callback
            self.add_item(button)

    def settle(self) -> int:
        """Pay out once the game is over (once only). Returns what was paid."""
        if not self.game.game_over or self.settled:
            return 0
        self.settled = True
        total_winnings, _ = self.game.calculate_winnings()
        if total_winnings:
            self.services.users.credit_wallet(self.game.user_id, total_winnings)
        return total_winnings

    async def _act(self, interaction: Interaction, action, extra_bet: bool = False, allowed=None) -> None:
        if self.game.game_over:                           # a click that arrived after the last move
            embed = discord.Embed(description="This game is already over.", color=Colors.UNAVAILABLE)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if allowed is not None and not allowed():         # a stale or double click: never charge for a refused move
            embed = discord.Embed(description="❌ You can't do that with this hand any more.", color=Colors.FAIL)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if extra_bet:                                     # doubling and splitting stake one more bet
            wallet = self.services.users.get(self.game.user_id)["wallet"]
            if wallet < self.game.bet_amount:
                embed = discord.Embed(description=f"❌ You need {CURRENCY}{self.game.bet_amount} more for that!",
                                      color=Colors.FAIL)
                return await interaction.response.send_message(embed=embed, ephemeral=True)
            self.services.users.credit_wallet(self.game.user_id, -self.game.bet_amount)
        action()
        self.settle()
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_game_embed(), view=self)

    async def hit_callback(self, interaction: Interaction):
        await self._act(interaction, self.game.hit)

    async def stand_callback(self, interaction: Interaction):
        await self._act(interaction, self.game.stand)

    async def double_callback(self, interaction: Interaction):
        await self._act(interaction, self.game.double_down, extra_bet=True, allowed=self.game.can_double_down)

    async def split_callback(self, interaction: Interaction):
        await self._act(interaction, self.game.split, extra_bet=True, allowed=self.game.can_split)

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
        if interaction.user.id == self.game.user_id:
            return True
        embed = discord.Embed(description="This isn't your game!", color=Colors.UNAVAILABLE)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False

    async def on_timeout(self):
        """The player walked away: their remaining hands stand, the dealer plays, and the game settles normally."""
        if not self.game.game_over:
            while not self.game.game_over:
                self.game.stand()
            self.settle()
            self.update_buttons()
            embed = self.create_game_embed()
            embed.description = "⏰ Time's up: your remaining hands stood."
            if self.original_interaction is not None:
                try:
                    await self.original_interaction.edit_original_response(embed=embed, view=None)
                except discord.HTTPException:
                    pass                                  # the message is gone; the game is settled anyway
        self.services.sessions.end(self.game.user_id)

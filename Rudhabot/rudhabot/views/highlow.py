"""High-Low: guess whether the next card is higher or lower. The bet is deducted up front by the command."""
from __future__ import annotations

import functools
import random

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.domain.cards import card_emojis
from rudhabot.ui.theme import CURRENCY
from rudhabot.views.base import ServiceView

RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']   # high-ace order
SUITS = ['♠', '♥', '♦', '♣']
RANK_VALUES = {rank: i for i, rank in enumerate(RANKS)}

# payout (x:1) for guessing "higher" / "lower" from each rank; None = impossible guess
ODDS_TABLE = {
    '2':   {'higher': 0.03,  'lower': None},
    '3':   {'higher': 0.12,  'lower': 11.37},
    '4':   {'higher': 0.24,  'lower': 5.18},
    '5':   {'higher': 0.37,  'lower': 3.12},
    '6':   {'higher': 0.55,  'lower': 2.09},
    '7':   {'higher': 0.77,  'lower': 1.47},
    '8':   {'higher': 1.06,  'lower': 1.06},
    '9':   {'higher': 1.47,  'lower': 0.77},
    '10':  {'higher': 2.09,  'lower': 0.55},
    'J':   {'higher': 3.12,  'lower': 0.37},
    'Q':   {'higher': 5.18,  'lower': 0.24},
    'K':   {'higher': 11.37, 'lower': 0.12},
    'A':   {'higher': None,  'lower': 0.03},
}


def draw_card(rng=random) -> tuple[str, str]:
    """One rank draw then one suit draw, as the original did."""
    return rng.choice(RANKS), rng.choice(SUITS)


def opening_embed(first_rank: str, first_suit: str, user) -> discord.Embed:
    emoji = card_emojis.get(f"{first_rank}{first_suit}", "🎴")
    embed = discord.Embed(
        title="↕️ HIGH-LOW",
        description=f"Card shown: {emoji} **{first_rank}{first_suit}**\n\nWill the next card be **higher** or **lower**?",
        color=0x99AAB5,
    )
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    odds = ODDS_TABLE[first_rank]
    text = f"**Higher**: {odds['higher']}:1\n" if odds['higher'] is not None else "**Higher**: Not available\n"
    text += f"**Lower**: {odds['lower']}:1" if odds['lower'] is not None else "**Lower**: Not available"
    embed.add_field(name="Payout Odds", value=text, inline=False)
    return embed


class HighLowView(ServiceView):
    def __init__(self, services, interaction: Interaction, bet_amount: int, first_rank: str, first_suit: str,
                 rng=random):
        super().__init__(services, timeout=300)
        self.interaction = interaction
        self.user_id = interaction.user.id
        self.bet_amount = bet_amount
        self.first_rank = first_rank
        self.first_value = RANK_VALUES[first_rank]
        self.rng = rng
        self.game_over = False
        odds = ODDS_TABLE[first_rank]
        self.higher_btn = ui.Button(label="Higher", style=ButtonStyle.success, disabled=(odds['higher'] is None))
        self.lower_btn = ui.Button(label="Lower", style=ButtonStyle.danger, disabled=(odds['lower'] is None))
        self.higher_btn.callback = functools.partial(self.finish, guess="higher")
        self.lower_btn.callback = functools.partial(self.finish, guess="lower")
        self.add_item(self.higher_btn)
        self.add_item(self.lower_btn)

    async def interaction_check(self, interaction: Interaction):
        return interaction.user.id == self.user_id

    async def finish(self, i: Interaction, guess):
        if self.game_over:
            return
        next_rank, next_suit = draw_card(self.rng)
        next_value = RANK_VALUES[next_rank]
        next_emoji = card_emojis.get(f"{next_rank}{next_suit}", "🎴")
        result_text = f"Next card: {next_emoji} **{next_rank}{next_suit}**\n"
        payout = ODDS_TABLE[self.first_rank][guess]
        users = self.services.users
        if next_value == self.first_value:
            result_text += "It's a tie! Your bet is returned."
            color = 0x99AAB5
            users.credit_wallet(self.user_id, self.bet_amount)
            result_text += f"\n\n💰 Bet refunded: {CURRENCY}{self.bet_amount}"
        elif (guess == "higher" and next_value > self.first_value) or (guess == "lower" and next_value < self.first_value):
            winnings = int(self.bet_amount + self.bet_amount * payout)
            users.credit_wallet(self.user_id, winnings)
            result_text += f"You guessed **{guess}** and were correct!\n\n🎉 You win! {CURRENCY}{winnings} (Payout: {payout}:1)"
            color = 0x57F287
        else:
            result_text += f"You guessed **{guess}** and were wrong.\n\n💸 You lose! {CURRENCY}{self.bet_amount}"
            color = 0xED4245
        self.game_over = True
        result = discord.Embed(title="↕️ HIGH-LOW", description=result_text, color=color)
        result.set_author(name=i.user.name, icon_url=i.user.display_avatar.url)
        await i.response.edit_message(embed=result, view=None)
        self.services.sessions.end(self.user_id)

    async def on_timeout(self):
        if self.game_over:
            return
        wallet = self.services.users.credit_wallet(self.user_id, self.bet_amount)      # refund the bet
        embed = discord.Embed(
            title="↕️ HIGH-LOW - TIMEOUT",
            description=f"⏰ **Game timed out!**\n💰 **Refunded:** {CURRENCY}{self.bet_amount}\n\n"
                        f"Your bet has been returned to your wallet.",
            color=0x979C9F,
        )
        embed.set_footer(text=f"Final wallet: {wallet}ra")
        await self.interaction.edit_original_response(embed=embed, view=None)
        self.services.sessions.end(self.user_id)

"""High-Low: guess whether the next card is higher or lower. The bet is deducted up front by the command.

A tie (the same rank again) loses: with ties refunded, every guess returned 102.5-103% (and "higher" on a 2 or
"lower" on an Ace could never lose). Now every guess returns about 95%. A player who walks away isn't refunded
(that was a free look at the first card): at timeout Rudhabot makes the likelier guess for them."""
from __future__ import annotations

import functools
import random

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.domain.cards import card_emojis
from rudhabot.ui.theme import CURRENCY, HIGHLOWICON, Colors
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
        title=f"{HIGHLOWICON} HIGH-LOW",
        description=f"Card shown: {emoji} **{first_rank}{first_suit}**\n\nWill the next card be **higher** or **lower**?",
        color=Colors.GAMBLING,
    )
    embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    odds = ODDS_TABLE[first_rank]
    text = f"**Higher**: {odds['higher']}:1\n" if odds['higher'] is not None else "**Higher**: Not available\n"
    text += f"**Lower**: {odds['lower']}:1" if odds['lower'] is not None else "**Lower**: Not available"
    text += "\n-# The same rank again (a tie) loses. If you don't pick within 5 minutes, the likelier guess is made."
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

    def likelier_guess(self) -> str:
        """The guess with more winning ranks (what a sensible player would pick)."""
        above = sum(1 for r in RANKS if RANK_VALUES[r] > self.first_value)
        below = sum(1 for r in RANKS if RANK_VALUES[r] < self.first_value)
        if ODDS_TABLE[self.first_rank]["lower"] is None:
            return "higher"
        if ODDS_TABLE[self.first_rank]["higher"] is None:
            return "lower"
        return "higher" if above >= below else "lower"

    def _resolve(self, guess: str, user, note: str = "") -> discord.Embed:
        """Draw the next card, settle the bet (a tie loses) and end the session. Returns the result embed."""
        self.game_over = True
        next_rank, next_suit = draw_card(self.rng)
        next_value = RANK_VALUES[next_rank]
        next_emoji = card_emojis.get(f"{next_rank}{next_suit}", "🎴")
        result_text = note + f"Next card: {next_emoji} **{next_rank}{next_suit}**\n"
        payout = ODDS_TABLE[self.first_rank][guess]
        if (guess == "higher" and next_value > self.first_value) or (guess == "lower" and next_value < self.first_value):
            winnings = int(self.bet_amount + self.bet_amount * payout)
            wallet = self.services.users.credit_wallet(self.user_id, winnings)
            result_text += f"You guessed **{guess}** and were correct!\n\n🎉 You win! {CURRENCY}{winnings} (Payout: {payout}:1)"
            color = Colors.SUCCESS
        else:
            wallet = self.services.users.get(self.user_id)["wallet"]
            why = "It's the same rank: a tie loses." if next_value == self.first_value else \
                f"You guessed **{guess}** and were wrong."
            result_text += f"{why}\n\n💸 You lose! {CURRENCY}{self.bet_amount}"
            color = Colors.LOSS
        result = discord.Embed(title=f"{HIGHLOWICON} HIGH-LOW", description=result_text, color=color)
        result.set_author(name=user.name, icon_url=user.display_avatar.url)
        result.set_footer(text=f"Final wallet: {wallet}ra")
        self.services.sessions.end(self.user_id)          # before any Discord call: a failed edit must not lock them out
        return result

    async def finish(self, i: Interaction, guess):
        if self.game_over:
            return
        await i.response.edit_message(embed=self._resolve(guess, i.user), view=None)

    async def on_timeout(self):
        if self.game_over:
            return
        guess = self.likelier_guess()
        embed = self._resolve(guess, self.interaction.user, note=f"⏰ Time's up: Rudhabot guessed **{guess}** for you.\n")
        try:
            await self.interaction.edit_original_response(embed=embed, view=None)
        except discord.HTTPException:
            pass                                          # the message is gone; the bet is settled and unlocked anyway

"""Gambling commands: coinflip, dice, rps, slots, roulette, highlow, crash, blackjack.

Self-contained games (coinflip, dice, rps, slots) hold the player lock for their whole duration through
`GamblingSessions.session()`. Button-driven games (roulette, highlow, crash, blackjack) take the lock once the bet
has been validated and their view releases it when the game ends. A refused bet never leaves a lock behind.
"""
from __future__ import annotations

import asyncio

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.domain.blackjack import BlackjackGame
from rudhabot.ui import slots_embeds
from rudhabot.ui.messages import MSG_INVALIDBET, MSG_NONPOS, MSG_NOWALLET, MSG_ONGAME
from rudhabot.ui.replies import respond
from rudhabot.ui.theme import CURRENCY
from rudhabot.views import crash as crash_game
from rudhabot.views import highlow as highlow_game
from rudhabot.views.blackjack import BlackjackView
from rudhabot.views.crash import CrashView
from rudhabot.views.highlow import HighLowView
from rudhabot.views.roulette import RouletteView

DICE_EMOJIS = {
    1: "<:dice_one:1399014570487713802>", 2: "<:dice_two:1399014591714951168>", 3: "<:dice_three:1399014609549131877>",
    4: "<:dice_four:1399014628154937515>", 5: "<:dice_five:1399014645091663974>", 6: "<:dice_six:1399014660119855164>",
}
RPS_EMOJIS = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
RPS_BEATS = {("rock", "scissors"), ("paper", "rock"), ("scissors", "paper")}


class BetRefused(Exception):
    """A bet that cannot be played; the message is shown (privately) to the player."""


def _plain(message: str, color: int = 0x992D22, user=None) -> discord.Embed:
    """An embed; `user` adds the author header (some game messages have one, others never did)."""
    embed = discord.Embed(description=message, color=color)
    if user is not None:
        embed.set_author(name=user.name, icon_url=user.display_avatar.url)
    return embed


class GamblingCog(ServiceCog):
    def __init__(self, bot):
        super().__init__(bot)
        self.sessions = self.services.sessions
        self.rng = self.services.rng

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _bet_amount(account: dict, text: str) -> int:
        """Format check only ('all' or an integer). Each game then applies the remaining checks in its own order."""
        if text.lower() == "all":
            return account["wallet"]
        try:
            return int(text)
        except (TypeError, ValueError):
            raise BetRefused(MSG_INVALIDBET) from None

    @staticmethod
    def _positive(amount: int) -> None:
        if amount <= 0:
            raise BetRefused(MSG_NONPOS)

    @staticmethod
    def _funds(account: dict, amount: int) -> None:
        if account["wallet"] < amount:
            raise BetRefused(MSG_NOWALLET)

    def _settle(self, user_id: int, delta: int) -> int:
        """Apply a win/loss to the *current* wallet (never below zero) and return the new balance. Reading the
        wallet at the end, instead of trusting a snapshot taken seconds earlier, means money received in the
        meantime isn't overwritten."""
        wallet = max(0, int(self.users.get(user_id)["wallet"]) + delta)
        self.users.update(user_id, wallet=wallet)
        return wallet

    async def _refuse(self, interaction, refusal: BetRefused, *, author_on_funds: bool = False) -> None:
        user = interaction.user if (author_on_funds and str(refusal) == MSG_NOWALLET) else None
        await respond(interaction, _plain(str(refusal), user=user), ephemeral=True)

    async def _busy(self, interaction) -> bool:
        if interaction.user.id in self.sessions:
            await respond(interaction, _plain(MSG_ONGAME), ephemeral=True)
            return True
        return False

    # ----------------------------------------------------------------- coinflip
    @app_commands.command(name="coinflip", description="Flip a coin and bet on the outcome")
    @app_commands.describe(bet="Amount to bet or 'all'", choice="heads or tails")
    @app_commands.choices(choice=[app_commands.Choice(name="Heads", value="heads"), app_commands.Choice(name="Tails", value="tails")])
    async def coinflip(self, interaction: discord.Interaction, bet: str, choice: str):
        if await self._busy(interaction):
            return
        with self.sessions.session(interaction.user.id):
            choice = choice.lower()
            if choice not in ("heads", "tails"):
                await respond(interaction, _plain("❌ Please choose 'heads' or 'tails'."), ephemeral=True)
                return
            account = self.users.get(interaction.user.id)
            try:
                amount = self._bet_amount(account, bet)
                self._positive(amount)
                self._funds(account, amount)
            except BetRefused as refusal:
                await self._refuse(interaction, refusal, author_on_funds=True)
                return
            await interaction.response.send_message(embed=_plain("🪙 Flipping a coin...", 0x99AAB5, interaction.user))
            await asyncio.sleep(3)
            result = self.rng.choice(["heads", "tails"])
            coin = "<:coinF_heads:1398951529955852328>" if result == "heads" else "<:coinF_tails:1398951563405557861>"
            if choice == result:
                self._settle(interaction.user.id, amount)
                embed = _plain(f"{coin} **{result.capitalize()}!**\n🎉 You win! {CURRENCY}{amount * 2}", 0x57F287, interaction.user)
            else:
                self._settle(interaction.user.id, -amount)
                embed = _plain(f"{coin} **{result.capitalize()}!**\n💸 You lose! {CURRENCY}{amount}", 0xED4245, interaction.user)
            await interaction.edit_original_response(embed=embed)

    # --------------------------------------------------------------------- dice
    @app_commands.command(name="dice", description="Roll dice and bet on the outcome")
    @app_commands.describe(bet="Amount to bet or 'all'", guess="Your guess (2-12)")
    async def dice(self, interaction: discord.Interaction, bet: str, guess: int):
        if await self._busy(interaction):
            return
        with self.sessions.session(interaction.user.id):
            account = self.users.get(interaction.user.id)
            try:
                amount = self._bet_amount(account, bet)
                self._positive(amount)
                if guess < 2 or guess > 12:
                    raise BetRefused("❌ Guess must be between 2 and 12.")
                self._funds(account, amount)
            except BetRefused as refusal:
                await self._refuse(interaction, refusal, author_on_funds=True)
                return
            await interaction.response.send_message(embed=_plain("🎲 Rolling the dice...", 0x99AAB5, interaction.user))
            await asyncio.sleep(3)
            die1, die2 = self.rng.randint(1, 6), self.rng.randint(1, 6)
            total = die1 + die2
            faces = f"{DICE_EMOJIS.get(die1, '🎲')} + {DICE_EMOJIS.get(die2, '🎲')} = **{total}**"
            if guess == total:
                multiplier = 6 if total in (2, 12) else 5 if total in (3, 11) else 4 if total in (4, 10) else 3
                self._settle(interaction.user.id, amount * multiplier - amount)
                embed = _plain(f"{faces}\n\n🎉 You win! {CURRENCY}{amount * multiplier} (x{multiplier})", 0x57F287, interaction.user)
            else:
                self._settle(interaction.user.id, -amount)
                embed = _plain(f"{faces}\n\n💸 You lose! {CURRENCY}{amount}", 0xED4245, interaction.user)
            await interaction.edit_original_response(embed=embed)

    # ---------------------------------------------------------------------- rps
    @app_commands.command(name="rps", description="Rock Paper Scissors gambling")
    @app_commands.describe(bet="Amount to bet or 'all'", choice="rock, paper, or scissors")
    @app_commands.choices(choice=[app_commands.Choice(name="Rock", value="rock"), app_commands.Choice(name="Paper", value="paper"),
                                  app_commands.Choice(name="Scissors", value="scissors")])
    async def rps(self, interaction: discord.Interaction, bet: str, choice: str):
        if await self._busy(interaction):
            return
        with self.sessions.session(interaction.user.id):
            choice = choice.lower()
            account = self.users.get(interaction.user.id)
            try:
                amount = self._bet_amount(account, bet)
                if choice not in RPS_EMOJIS:
                    raise BetRefused("❌ Please choose 'rock', 'paper', or 'scissors'.")
                self._positive(amount)
                self._funds(account, amount)
            except BetRefused as refusal:
                await self._refuse(interaction, refusal)
                return
            bot_choice = self.rng.choice(list(RPS_EMOJIS))
            faces = f"{RPS_EMOJIS[choice]} vs {RPS_EMOJIS[bot_choice]}"
            if choice == bot_choice:
                embed = _plain(f"{faces}\n\n🤝 It's a tie! Your bet is pulled back.", 0x99AAB5, interaction.user)
            elif (choice, bot_choice) in RPS_BEATS:
                self._settle(interaction.user.id, amount)
                embed = _plain(f"{faces}\n\n🎉 You win! {CURRENCY}{amount}", 0x57F287, interaction.user)
            else:
                self._settle(interaction.user.id, -amount)
                embed = _plain(f"{faces}\n\n💸 You lose! {CURRENCY}{amount}", 0xED4245, interaction.user)
            await respond(interaction, embed)

    # -------------------------------------------------------------------- slots
    @app_commands.command(name="slots", description="Play the slot machine")
    @app_commands.describe(bet="Amount to bet or 'all'")
    async def slots(self, interaction: discord.Interaction, bet: str):
        """Rules unchanged (see rudhabot.domain.slots); only the presentation is new (see ui.slots_embeds)."""
        if await self._busy(interaction):
            return
        user = interaction.user
        with self.sessions.session(user.id):
            account = self.users.get(user.id)
            try:
                amount = self._bet_amount(account, bet)
                self._positive(amount)
                self._funds(account, amount)
            except BetRefused as refusal:
                await self._refuse(interaction, refusal)
                return
            machine = self.services.slots
            stopped = []
            await interaction.response.send_message(embed=slots_embeds.spinning_frame(user, stopped, amount))
            for reel in range(machine.REELS):
                await asyncio.sleep(2)
                stopped.append(machine.spin_reel())
                if reel < machine.REELS - 1:                       # the last reel goes straight to the result
                    await interaction.edit_original_response(embed=slots_embeds.spinning_frame(user, stopped, amount))
            outcome = machine.evaluate(stopped)
            wallet_after = self._settle(user.id, outcome.net(amount))
            await interaction.edit_original_response(embed=slots_embeds.result_frame(user, stopped, outcome, amount, wallet_after))

    # ----------------------------------------------------------------- roulette
    @app_commands.command(name="roulette", description="Play American Roulette")
    async def roulette(self, interaction: discord.Interaction):
        if await self._busy(interaction):
            return
        account = self.users.get(interaction.user.id)
        if account["wallet"] <= 0:
            await respond(interaction, _plain(MSG_NOWALLET), ephemeral=True)
            return
        embed = discord.Embed(
            title="<a:roulette_wheel:1400109956761522206> ROULETTE",
            description=f"**Total Bet:** {CURRENCY}0\n**Current Bets:** None\n\nClick betting options below to place your bets!",
            color=0x99AAB5,
        )
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Wallet", value=f"{CURRENCY}{account['wallet']}")
        embed.set_footer(text="Each bet is deducted immediately.")
        view = RouletteView(interaction.user.id, self.services)
        view.original_interaction = interaction
        await interaction.response.send_message(embed=embed, view=view)

    # ------------------------------------------------------------ interactive games
    async def _take_bet(self, interaction, bet: str):
        """Validate the bet, deduct it and take the player lock. Returns the amount, or None if refused."""
        if await self._busy(interaction):
            return None
        account = self.users.get(interaction.user.id)
        try:
            amount = self._bet_amount(account, bet)
            self._positive(amount)
            self._funds(account, amount)
        except BetRefused as refusal:
            await self._refuse(interaction, refusal)
            return None
        self.users.update(interaction.user.id, wallet=account["wallet"] - amount)
        self.sessions.start(interaction.user.id)
        return amount

    async def _send_or_release(self, interaction, amount: int, **message):
        """Send the opening message; if that fails, refund the bet and release the lock so nothing is stranded."""
        try:
            await interaction.response.send_message(**message)
        except Exception:
            self.users.credit_wallet(interaction.user.id, amount)
            self.sessions.end(interaction.user.id)
            raise

    @app_commands.command(name="highlow", description="Play High-Low: Guess if the next card is higher or lower")
    @app_commands.describe(bet="Amount to bet or 'all'")
    async def highlow(self, interaction: discord.Interaction, bet: str):
        amount = await self._take_bet(interaction, bet)
        if amount is None:
            return
        rank, suit = highlow_game.draw_card(self.rng)
        view = HighLowView(self.services, interaction, amount, rank, suit, self.rng)
        await self._send_or_release(interaction, amount, embed=highlow_game.opening_embed(rank, suit, interaction.user), view=view)

    @app_commands.command(name="crash", description="Cash out before the multiplier crashes!")
    @app_commands.describe(bet="Amount to bet or 'all'")
    async def crash(self, interaction: discord.Interaction, bet: str):
        amount = await self._take_bet(interaction, bet)
        if amount is None:
            return
        view = CrashView(self.services, interaction, amount, self.rng)
        await self._send_or_release(interaction, amount, embed=crash_game.opening_embed(amount, interaction.user), view=view)

    @app_commands.command(name="blackjack", description="Play Blackjack")
    @app_commands.describe(bet="Amount to bet or 'all'")
    async def blackjack(self, interaction: discord.Interaction, bet: str):
        if await self._busy(interaction):
            return
        account = self.users.get(interaction.user.id)
        try:
            amount = self._bet_amount(account, bet)
            self._positive(amount)
            self._funds(account, amount)
        except BetRefused as refusal:
            await self._refuse(interaction, refusal)
            return
        self.users.update(interaction.user.id, wallet=account["wallet"] - amount)
        game = BlackjackGame(interaction.user.id, amount, self.sessions, self.rng)
        view = BlackjackView(game, self.services)
        embed = view.create_game_embed()
        if game.player_hands[0].is_blackjack():                      # natural blackjack settles immediately
            game.dealer_play()
            game.end_game()
            view.update_buttons()
            total_winnings, _ = game.calculate_winnings()
            self.users.credit_wallet(interaction.user.id, total_winnings)
            embed = view.create_game_embed()
        await interaction.response.send_message(embed=embed, view=view)

"""Help command data, embed builder, and paginated help views."""
from __future__ import annotations

import math

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.ui.theme import BOTICON


class HelpView(ui.View):
    def __init__(self, commands_data, current_page=0):
        super().__init__(timeout=60)
        self.commands_data = commands_data
        self.current_page = current_page
        self.total_pages = math.ceil(len(commands_data) / 5)
        
        # Add navigation buttons
        self.add_item(HelpPageButton("⬅ Previous", -1))
        self.add_item(HelpPageButton("Next ⮕", 1))

    async def interaction_check(self, interaction: Interaction):
        return True

class HelpPageButton(ui.Button):
    def __init__(self, label, direction):
        super().__init__(label=label, style=ButtonStyle.primary)
        self.direction = direction

    async def callback(self, interaction: Interaction):
        view: HelpView = self.view
        new_page = (view.current_page + self.direction) % view.total_pages
        embed = create_help_embed(view.commands_data, new_page, view.total_pages)
        view.current_page = new_page
        await interaction.response.edit_message(embed=embed, view=view)

def create_help_embed(commands_data, page, total_pages):
    embed = discord.Embed(
        title=f"{BOTICON} HELP",
        description="**Rudhabot** is a bot specialized for the Rudharta Discord server, with features tuned for the community's needs. **Rudhabot** is developed by the Digital Research Institute, and as such is the property of the Kingdom of Rudharta created by ``fendrizian``. Copyright 2025.",
        color=0x607d8b
    )
    embed.set_thumbnail(url="https://micronations.wiki/images/en/images/images/6/6a/Flag_of_Rudharta.png")
    
    start = page * 5
    end = start + 5
    page_commands = commands_data[start:end]
    
    for command_info in page_commands:
        name = command_info["name"]
        args = command_info["args"]
        description = command_info["description"]
        embed.add_field(
            name=f"/{name} {args}",
            value=description,
            inline=False
        )
    
    embed.set_footer(text=f"Page {page + 1}/{total_pages}")
    return embed

def get_commands_data():
    """Returns a list of all commands with their info"""
    return [
        {
            "name": "help",
            "args": "",
            "description": "Shows this help message with all available commands."
        },
        {
            "name": "balance",
            "args": "[user]",
            "description": "Check your wallet and bank balance, or view another user's balance."
        },
        {
            "name": "work",
            "args": "",
            "description": "Work and earn money! Has a 20-minute cooldown."
        },
        {
            "name": "daily",
            "args": "",
            "description": "Claim your daily reward of 200ra. Available once every 24 hours."
        },
        {
            "name": "crime",
            "args": "",
            "description": "Commit a crime to earn money, but risk getting fined. 30-minute cooldown."
        },
        {
            "name": "deposit",
            "args": "<amount|all>",
            "description": "Deposit money from your wallet to your bank account."
        },
        {
            "name": "withdraw",
            "args": "<amount|all>",
            "description": "Withdraw money from your bank to your wallet."
        },
        {
            "name": "rob",
            "args": "<user>",
            "description": "Attempt to rob another user. Success steals money, failure results in a fine."
        },
        {
            "name": "send",
            "args": "<user> <amount|all>",
            "description": "Give money from your wallet to another user."
        },
        {
            "name": "shop",
            "args": "",
            "description": "Browse items available in the National Online Shop."
        },
        {
            "name": "cafe",
            "args": "",
            "description": "View the Joot Cafe menu with special monthly items."
        },
        {
            "name": "buy",
            "args": "<item> [quantity]",
            "description": "Purchase items from shops or the cafe."
        },
        {
            "name": "use",
            "args": "<item>",
            "description": "Use an item from your inventory: draw lottery tickets, open mystery boxes, set up birthday cakes and more."
        },
        {
            "name": "loan",
            "args": "<amount> <days>",
            "description": "Take a loan from the bank. Amount: 100-10000ra, Days: 1-30."
        },
        {
            "name": "pay",
            "args": "<amount|all>",
            "description": "Pay off your loan debt."
        },
        {
            "name": "extend",
            "args": "[days]",
            "description": "Extend your loan for additional days (10% fee, once per loan)."
        },
        {
            "name": "loaninfo",
            "args": "",
            "description": "Check your current loan status and debt information."
        },
        {
            "name": "crypto",
            "args": "",
            "description": "View the current cryptocurrency market prices."
        },
        {
            "name": "buycoin",
            "args": "<coin> <amount>",
            "description": "Buy cryptocurrency with your wallet money."
        },
        {
            "name": "sellcoin",
            "args": "<coin> <amount|all>",
            "description": "Sell your cryptocurrency holdings."
        },
        {
            "name": "portfolio",
            "args": "",
            "description": "View your investment portfolio and total value."
        },
        {
            "name": "coinflip",
            "args": "<bet> <heads|tails>",
            "description": "Flip a coin and bet on the outcome. 2x payout on correct guess."
        },
        {
            "name": "slots",
            "args": "<bet>",
            "description": "Play the slot machine with various payout multipliers."
        },
        {
            "name": "dice",
            "args": "<bet> <guess>",
            "description": "Roll dice and guess the sum (2-12). Higher payouts for harder guesses."
        },
        {
            "name": "rps",
            "args": "<bet> <rock|paper|scissors>",
            "description": "Play Rock Paper Scissors against the bot."
        },
        {
            "name": "roulette",
            "args": "",
            "description": "Play American Roulette with various betting options."
        },
        {
            "name": "highlow",
            "args": "<bet>",
            "description": "Play High-Low: Guess if the next card is higher or lower. Payouts based on odds table."
        },
        {
            "name": "crash",
            "args": "<bet>",
            "description": "Play Crash: Cash out before the multiplier crashes for a risky reward."
        },
        {
            "name": "blackjack",
            "args": "<bet>",
            "description": "Play Blackjack against the dealer."
        },
        {
            "name": "leaderboard",
            "args": "[wallet|bank|total]",
            "description": "View the server leaderboard for money rankings."
        },
        {
            "name": "add-money",
            "args": "<user> <amount> [wallet|bank]",
            "description": "Add money to a user's account (Admin and Banker)."
        },
        {
            "name": "remove-money",
            "args": "<user> <amount> [wallet|bank]",
            "description": "Remove money from a user's account (Admin and Banker)."
        },
        {
            "name": "lottery",
            "args": "",
            "description": "See the lottery prize tables, your odds, your tickets and recent jackpot winners."
        },
        {
            "name": "birthday set",
            "args": "<day> <month> [year]",
            "description": "Register your birthday. You get a gift and a shout-out every year on the day."
        },
        {
            "name": "birthday view",
            "args": "[user]",
            "description": "See your (or someone else's) birthday and how long until the next one."
        },
        {
            "name": "birthday remove",
            "args": "",
            "description": "Remove your registered birthday."
        },
        {
            "name": "birthday upcoming",
            "args": "",
            "description": "List the next birthdays in this server."
        },
    ]

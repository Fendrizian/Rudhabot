"""/help: a sectioned overview, one page per section, and the full paginated list.

Navigation uses DynamicItems with fixed custom ids ("help:..."), so the menus never time out and keep working after a
restart. When someone other than the person who ran /help clicks, they get their own private copy instead of changing
the shared message.
"""
from __future__ import annotations

import math
import re
from typing import Optional

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.ui.theme import BOTICON

PER_PAGE = 6
COLOR = 0x607D8B
ABOUT = ("**Rudhabot** is a bot specialized for the Rudharta Discord server, with features tuned for the community's "
         "needs. **Rudhabot** is developed by the Digital Research Institute, and as such is the property of the Kingdom "
         "of Rudharta created by ``fendrizian``. Copyright 2025.")
THUMBNAIL = "https://micronations.wiki/images/en/images/images/6/6a/Flag_of_Rudharta.png"

# (key, title, emoji, one-line summary, command names in display order)
SECTIONS: list[tuple[str, str, str, str, tuple[str, ...]]] = [
    ("start", "Getting started", "👋", "Help and the /ask assistant (or just @mention Rudhabot)", ("help", "ask")),
    ("economy", "Economy", "💰", "Earn, send and compare money",
     ("balance", "work", "daily", "crime", "send", "rob", "leaderboard")),
    ("bank", "Bank", "🏦", "Deposits, savings, card tiers, credit cards and loans",
     ("deposit", "withdraw", "bank account | tiers | history", "bank save | unsave", "bank upgrade",
      "bank card info | pay", "loan", "loaninfo", "pay", "extend")),
    ("shop", "Shop & Café", "🛍️", "Buy, sell and use items", ("shop", "cafe", "buy", "sell", "inventory", "use")),
    ("markets", "Markets", "📈", "The stock exchange and crypto",
     ("portfolio", "stocks market | quote", "stocks buy | sell", "crypto", "buycoin", "sellcoin")),
    ("games", "Gambling games", "🎰", "Bet on coins, dice, cards and more",
     ("coinflip", "dice", "rps", "slots", "roulette", "highlow", "crash", "blackjack", "baccarat", "craps")),
    ("tcg", "Cloudfall TCG", "⛅", "Asgrelia: Cloudfall, the trading card game",
     ("tcg starter", "tcg practice", "tcg challenge", "tcg wish banner | pull", "tcg wish shards | daily | craft | convergence",
      "tcg deck build | view | use | analyze", "tcg card | rules | collection | pack | ladder",
      "tcg match | forfeit | forget-me")),
    ("community", "Lottery & birthdays", "🎉", "Lottery odds and birthday celebrations",
     ("lottery", "birthday set", "birthday view", "birthday remove", "birthday upcoming")),
    ("admin", "Admin", "🛠️", "Server administrators only",
     ("add-money", "remove-money", "create", "audit", "review list | resolve", "clairvoyance status | reload")),
]
SECTION_BY_KEY = {key: (title, emoji, summary, names) for key, title, emoji, summary, names in SECTIONS}


def _by_name() -> dict[str, dict]:
    return {c["name"]: c for c in get_commands_data()}


def _field(embed: discord.Embed, command: dict) -> None:
    embed.add_field(name=f"/{command['name']} {command['args']}".strip(), value=command["description"], inline=False)


# ------------------------------------------------------------------ embeds
def overview_embed() -> discord.Embed:
    embed = discord.Embed(title=f"{BOTICON} HELP", color=COLOR,
                          description=ABOUT + "\n\nPick a section below, or press **Full list** to see every command. "
                                              "Tip: `/ask` answers questions in plain words.")
    embed.set_thumbnail(url=THUMBNAIL)
    commands = _by_name()
    for key, title, emoji, summary, names in SECTIONS:
        sample = " ".join(f"`/{n.split(' |')[0]}`" for n in names[:4]) + (" …" if len(names) > 4 else "")
        embed.add_field(name=f"{emoji} {title} ({sum(1 for n in names if n in commands)})", value=f"{summary}\n{sample}",
                        inline=True)
    embed.set_footer(text=f"{len(commands)} commands in {len(SECTIONS)} sections")
    return embed


def section_embed(key: str) -> discord.Embed:
    title, emoji, summary, names = SECTION_BY_KEY[key]
    commands = _by_name()
    embed = discord.Embed(title=f"{emoji} {title}", description=summary, color=COLOR)
    for name in names:
        if name in commands:
            _field(embed, commands[name])
    position = [k for k, *_ in SECTIONS].index(key) + 1
    embed.set_footer(text=f"Section {position}/{len(SECTIONS)} · use the menu to switch sections")
    return embed


def ordered_commands() -> list[dict]:
    """Every command, grouped in section order (anything unsectioned last)."""
    commands = _by_name()
    order = [n for *_, names in SECTIONS for n in names if n in commands]
    return [commands[n] for n in order] + [c for n, c in commands.items() if n not in order]


def full_pages() -> int:
    return math.ceil(len(get_commands_data()) / PER_PAGE)


def create_help_embed(commands_data, page, total_pages):
    """The full list, one page at a time (kept for the "Full list" view)."""
    embed = discord.Embed(title=f"{BOTICON} HELP · all commands", description=ABOUT, color=COLOR)
    embed.set_thumbnail(url=THUMBNAIL)
    for command in commands_data[page * PER_PAGE:(page + 1) * PER_PAGE]:
        _field(embed, command)
    embed.set_footer(text=f"Page {page + 1}/{total_pages}")
    return embed


# ------------------------------------------------------------------ navigation (never expires)
async def _show(interaction: Interaction, embed: discord.Embed, view: ui.View) -> None:
    """Edit the help message for the person who opened it; give anyone else their own private copy."""
    message = getattr(interaction, "message", None)
    meta = getattr(message, "interaction_metadata", None) if message is not None else None
    owner = getattr(getattr(meta, "user", None), "id", None)
    if owner is None or owner == interaction.user.id or getattr(message, "flags", None) and message.flags.ephemeral:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class SectionSelect(ui.DynamicItem[ui.Select], template=r"help:menu"):
    def __init__(self, current: Optional[str] = None):
        options = [discord.SelectOption(label="Overview", value="home", emoji="🏠", default=current == "home")]
        options += [discord.SelectOption(label=title, value=key, emoji=emoji, description=summary[:100], default=key == current)
                    for key, title, emoji, summary, _ in SECTIONS]
        options.append(discord.SelectOption(label="Full list (all commands)", value="full", emoji="📖", default=current == "full"))
        super().__init__(ui.Select(custom_id="help:menu", placeholder="Jump to a section…", options=options))

    @classmethod
    async def from_custom_id(cls, interaction: Interaction, item: ui.Select, match: re.Match[str], /):
        return cls()

    async def callback(self, interaction: Interaction) -> None:
        choice = interaction.data["values"][0]
        embed, view = render(choice)
        await _show(interaction, embed, view)


class NavButton(ui.DynamicItem[ui.Button], template=r"help:(?P<target>home|full):(?P<page>\d+)"):
    def __init__(self, target: str, page: int = 0, label: str = "", disabled: bool = False, emoji: Optional[str] = None):
        super().__init__(ui.Button(label=label, emoji=emoji, disabled=disabled, style=ButtonStyle.primary
                                   if target == "full" else ButtonStyle.secondary, custom_id=f"help:{target}:{page}"))
        self.target, self.page = target, page

    @classmethod
    async def from_custom_id(cls, interaction: Interaction, item: ui.Button, match: re.Match[str], /):
        return cls(match["target"], int(match["page"]))

    async def callback(self, interaction: Interaction) -> None:
        embed, view = render(self.target, self.page)
        await _show(interaction, embed, view)


def help_view(current: str = "home", page: int = 0) -> ui.View:
    view = ui.View(timeout=None)
    view.add_item(SectionSelect(current))
    if current == "full":
        pages = full_pages()
        # custom ids must be unique within a message, so the prev/next ids differ even at the ends
        view.add_item(NavButton("full", max(0, page - 1) if page > 0 else pages + 1, "◀ Previous", disabled=page == 0))
        view.add_item(NavButton("full", page + 1 if page < pages - 1 else pages + 2, "Next ▶", disabled=page >= pages - 1))
        view.add_item(NavButton("home", 0, "Overview", emoji="🏠"))
    else:
        view.add_item(NavButton("full", 0, "Full list", emoji="📖"))
        if current != "home":
            view.add_item(NavButton("home", 0, "Overview", emoji="🏠"))
    return view


def render(target: str, page: int = 0) -> tuple[discord.Embed, ui.View]:
    if target == "full":
        pages = full_pages()
        page = max(0, min(page, pages - 1))
        return create_help_embed(ordered_commands(), page, pages), help_view("full", page)
    if target in SECTION_BY_KEY:
        return section_embed(target), help_view(target)
    return overview_embed(), help_view("home")


def get_commands_data():
    """Returns a list of all commands with their info"""
    return [
        {
            "name": "help",
            "args": "",
            "description": "Shows this help message with all available commands."
        },
        {
            "name": "ask",
            "args": "<question> [private]",
            "description": "Ask Rudhabot how anything works. Answers come from Rudhabot's own docs (AI-written when OpenAI is configured)."
        },
        {
            "name": "bank account | tiers | history",
            "args": "",
            "description": "Your private bank statement (balances, monthly limits, savings, credit, Clairvoyance tips), the card tiers, and your recent transactions."
        },
        {
            "name": "bank save | unsave",
            "args": "<amount|all>",
            "description": "Move bank money into savings (daily interest by card tier) and back (once a day)."
        },
        {
            "name": "bank upgrade",
            "args": "",
            "description": "Upgrade your card: Red → Silver → Gold → Platinum for higher limits, interest and credit."
        },
        {
            "name": "bank card info | pay",
            "args": "[amount] [source]",
            "description": "Your credit card: balance, limit and statement; pay it from your wallet or bank. Pay for things with pay_with:card."
        },
        {
            "name": "stocks market | quote",
            "args": "[symbol]",
            "description": "The Rudharta Exchange: NAS (National Shop) and JOOT (Joot Cafe), priced by what the server buys."
        },
        {
            "name": "stocks buy | sell",
            "args": "<symbol> <shares>",
            "description": "Trade shares from your wallet (0.5% fee; big orders move the price). See them in /portfolio."
        },
        {
            "name": "baccarat",
            "args": "<bet> <player|banker|tie>",
            "description": "Punto banco baccarat: Player 1:1, Banker 1:1 minus 5%, Tie 8:1."
        },
        {
            "name": "craps",
            "args": "<bet> <pass|dontpass|field>",
            "description": "Craps: Pass Line, Don't Pass or the one-roll Field bet."
        },
        {
            "name": "tcg wish banner | pull",
            "args": "[count] [banner]",
            "description": "Skyfall Wishes: this week's featured banner, published rates and pity; make 1 or 10 wishes."
        },
        {
            "name": "tcg wish shards | daily | craft | convergence",
            "args": "",
            "description": "Get Cloud Shards (daily, buy, or win matches), craft cards with Vapor Dust, and claim the server-wide Convergence."
        },
        {
            "name": "audit",
            "args": "[user]",
            "description": "Admin: the latest economy audit entries (every money change is logged)."
        },
        {
            "name": "review list | resolve",
            "args": "",
            "description": "Admin: Clairvoyance's unusual-activity signals, for human review only (nothing is changed automatically)."
        },
        {
            "name": "tcg starter",
            "args": "<deck>",
            "description": "Asgrelia: Cloudfall: claim a free starter deck (one of six archetypes)."
        },
        {
            "name": "tcg practice",
            "args": "[difficulty] [opponent]",
            "description": "Play Cloudfall against the Clairvoyance, Rudhabot's own trained AI, with coaching hints."
        },
        {
            "name": "tcg challenge",
            "args": "<opponent> [coaching]",
            "description": "Challenge a player to a rated Cloudfall duel in its own thread, with a live win-chance bar."
        },
        {
            "name": "tcg deck build | view | use | analyze",
            "args": "",
            "description": "Build decks from your collection; `analyze` runs the Clairvoyance's Deck Doctor against every archetype."
        },
        {
            "name": "tcg card | rules | collection | pack | ladder",
            "args": "",
            "description": "Look up cards (with the Clairvoyance's similar cards), read the rules, open packs and see the rated ladder."
        },
        {
            "name": "tcg match | forfeit | forget-me",
            "args": "",
            "description": "Re-post your match board, concede, or delete your Cloudfall data."
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
            "description": "Claim your daily reward of 50ra. Available once every 24 hours."
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
            "name": "sell",
            "args": "<item> [quantity]",
            "description": "Sell shop items for half their price."
        },
        {
            "name": "inventory",
            "args": "",
            "description": "Show all items you own and their quantity."
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
            "description": "Your investments in one place: crypto holdings and stocks (with profit/loss), plus the total."
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
            "name": "create",
            "args": "",
            "description": "Admin: create a temporary private bot-lab channel."
        },
        {
            "name": "add-money",
            "args": "<user> <amount> [wallet|bank]",
            "description": "Add money to a user's account (Admin and Banker)."
        },
        {
            "name": "clairvoyance status | reload",
            "args": "[version]",
            "description": "Admin: the Clairvoyance model's version, measured quality, latency and health; reload or roll back."
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

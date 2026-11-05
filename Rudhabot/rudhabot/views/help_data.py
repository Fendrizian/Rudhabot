"""What /help shows: the sections, which rank each staff entry needs, and every command's help text.

Kept apart from the rendering (views/help.py) so the text can be edited without touching the code. Tests make sure
every registered command is documented here and placed in exactly one section, and that every staff entry has a
requirement in REQUIRES.
"""
from __future__ import annotations

from rudhabot.ui.theme import TCG

# (key, title, emoji, one-line summary, command names; displayed alphabetically by alphabetical())
SECTIONS: list[tuple[str, str, str, str, tuple[str, ...]]] = [
    ("start", "Getting started", "👋", "Help, the guidebook and the /ask assistant (or just @mention Rudhabot)",
     ("help", "guide", "ask")),
    ("economy", "Economy", "💰", "Earn, send and compare money",
     ("work", "daily", "crime", "collect", "income", "send", "rob", "leaderboard")),
    ("bank", "Bank", "🏦", "Deposits, interest, card tiers, credit cards and loans",
     ("balance", "deposit", "withdraw", "bank tiers | history", "bank upgrade",
      "bank card info | pay", "loan", "loaninfo", "pay", "extend")),
    ("shop", "Shop & Café", "🛍️", "Buy, sell and use items", ("shop", "cafe", "buy", "sell", "inventory", "look", "use")),
    ("markets", "Markets", "📈", "The stock exchange and crypto",
     ("portfolio", "stocks market | quote", "stocks buy | sell", "crypto market", "crypto buy | sell")),
    ("games", "Gambling games", "🎰", "Bet on coins, dice, cards and more",
     ("coinflip", "dice", "rps", "slots", "roulette", "highlow", "crash", "blackjack", "baccarat", "craps")),
    ("tcg", "Cloudfall TCG", TCG.LOGO, "Asgrelia: Cloudfall, the trading card game",
     ("tcg starter", "tcg practice", "tcg challenge", "tcg wish banner | pull", "tcg wish shards | daily | craft | convergence",
      "tcg deck build | view | use | analyze", "tcg card | rules | collection | pack | ladder",
      "tcg match | forfeit | forget-me")),
    ("community", "Lottery & birthdays", "🎉", "Lottery tickets and birthday celebrations",
     ("lottery", "birthday set", "birthday view", "birthday remove", "birthday upcoming")),
    ("banker", "Banker", "💼", "The banker role (and admins): look after member accounts",
     ("audit", "review list | resolve", "banker account", "banker card-hold | card-release", "banker loan-unban")),
    ("admin", "Admin", "🛠️", "Server administrators only",
     ("add-money", "remove-money", "clairvoyance report", "clairvoyance status | reload", "set-income", "setup")),
    ("moderation", "Moderation", "🛡️", "Warnings, timeouts, kicks, bans and the moderation log (staff)",
     ("warn", "timeout", "untimeout", "kick", "ban", "massban", "unban", "purge", "slowmode",
      "case view | edit | delete", "modlog", "Report to moderators")),
    ("security", "Server security", "🚨", "Raid and anti-nuke defences run by themselves; these are the controls (staff)",
     ("security status", "security incident", "security lockdown", "security unlock", "security level",
      "security release", "security ask", "security setup", "security staff", "security settings",
      "security exempt", "security tools")),
]
SECTION_BY_KEY = {key: (title, emoji, summary, names) for key, title, emoji, summary, names in SECTIONS}


#: What each staff entry needs before it is shown (the lowest rank that can use it at all; security tiers from
#: security/clearance.py REQUIRED). Everything not listed is for everyone. Visibility only: commands check again.
REQUIRES: dict[str, str] = {
    # banker role (and admins)
    "audit": "banker", "review list | resolve": "banker", "banker account": "banker",
    "banker card-hold | card-release": "banker", "banker loan-unban": "banker",
    # admins
    "add-money": "admin", "remove-money": "admin", "clairvoyance report": "admin",
    "clairvoyance status | reload": "admin", "setup": "admin", "set-income": "admin",
    # moderation, by clearance tier
    "warn": "helper", "timeout": "helper", "untimeout": "helper", "purge": "helper", "slowmode": "helper",
    "case view | edit | delete": "helper", "modlog": "helper",
    "kick": "moderator",
    "ban": "senior", "massban": "senior", "unban": "senior",
    # security
    "security status": "helper", "security incident": "helper", "security release": "helper", "security ask": "helper",
    "security lockdown": "moderator", "security unlock": "moderator", "security setup": "moderator",
    "security staff": "moderator", "security settings": "moderator", "security exempt": "moderator",
    "security tools": "moderator",
    "security level": "senior",
}


def get_commands_data():
    """Returns a list of all commands with their info"""
    return [
        {
            "name": "help",
            "args": "",
            "description": "Shows this help message with all available commands."
        },
        {
            "name": "guide",
            "args": "[chapter] [public]",
            "description": "The Rudhabot Guidebook: how everything works, in plain words, chapter by chapter. Private unless you set public (member chapters only); staff also see the chapters for their rank."
        },
        {
            "name": "ask",
            "args": "<question> [private]",
            "description": "Ask Rudhabot how anything works. Answers come from Rudhabot's own docs (AI-written when OpenAI is configured)."
        },
        {
            "name": "bank tiers | history",
            "args": "",
            "description": "Compare the card tiers, and see your recent transactions."
        },
        {
            "name": "bank upgrade",
            "args": "",
            "description": "Upgrade your card: Red → Silver → Gold → Platinum for higher limits, bank interest and credit."
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
            "description": "Get Cloud Shards (daily, buy at 10ra each, or win matches), craft cards with Vapor Dust, and claim the server-wide Convergence."
        },
        {
            "name": "audit",
            "args": "[user]",
            "description": "Banker: the latest economy audit entries (every money change is logged)."
        },
        {
            "name": "review list | resolve",
            "args": "",
            "description": "Banker: Clairvoyance's unusual-activity signals, for human review only (nothing is changed automatically)."
        },
        {
            "name": "banker account",
            "args": "<user>",
            "description": "Banker: a member's wallet, bank, card, monthly limits, loan, card holds and loan bans (private, read-only)."
        },
        {
            "name": "banker card-hold | card-release",
            "args": "<user> <reason>",
            "description": "Banker: decline a member's credit card until a banker releases it. Logged in the audit channel."
        },
        {
            "name": "banker loan-unban",
            "args": "<user> <reason>",
            "description": "Banker: lift a bankruptcy loan ban early (and the bankrupt role). Logged in the audit channel."
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
            "description": "Look up cards (with the Clairvoyance's similar cards), read the rules, open packs (wallet or credit card) and see the rated ladder."
        },
        {
            "name": "tcg match | forfeit | forget-me",
            "args": "",
            "description": "Re-post your match board, concede, or delete your Cloudfall data."
        },
        {
            "name": "balance",
            "args": "[user]",
            "description": "Wallet, bank (it earns daily interest), total and card tier (anyone's). For yourself also your monthly limits, credit card and private Clairvoyance tips."
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
            "description": "Attempt to rob another user. Success steals money, failure results in a fine. Accounts security has on probation or in quarantine can't rob or be robbed."
        },
        {
            "name": "collect",
            "args": "",
            "description": "Collect the income your roles earn. Each income role has its own cooldown (usually a day); see /income for the list."
        },
        {
            "name": "income",
            "args": "",
            "description": "Every role income: how much each role earns and how often, with yours marked and when they're ready."
        },
        {
            "name": "send",
            "args": "<user> <amount|all>",
            "description": "Give money from your wallet to another user. Paused for accounts security has on probation or in quarantine."
        },
        {
            "name": "shop",
            "args": "",
            "description": "Browse and buy from the National Online Shop. The Paying with button switches between wallet and credit card."
        },
        {
            "name": "cafe",
            "args": "",
            "description": "View and order from the Joot Cafe menu, monthly specials included. Pay from your wallet or by credit card."
        },
        {
            "name": "buy",
            "args": "<item> [quantity] [pay_with]",
            "description": "Purchase items from the shop or the cafe, from your wallet or by credit card."
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
            "name": "look",
            "args": "<item>",
            "description": "Look at an item by name or alias: its lore (or description), price, resale value, what /use does, how many you own and its aliases."
        },
        {
            "name": "use",
            "args": "<item>",
            "description": "Use an item from your inventory: draw lottery tickets, open mystery boxes, crack fortune cookies, set up birthday cakes (`/use cake`) and more."
        },
        {
            "name": "loan",
            "args": "<amount> <days>",
            "description": "Take a loan from the bank. Amount: 100-10000ra, Days: 1-30. Not available while security has your account on probation or in quarantine."
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
            "name": "crypto market",
            "args": "",
            "description": "View the current cryptocurrency market prices."
        },
        {
            "name": "crypto buy | sell",
            "args": "<coin> <amount>",
            "description": "Buy a coin with wallet money (amount in ra), or sell a coin (an amount of coins, or 'all')."
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
            "name": "add-money",
            "args": "<user> <amount> [wallet|bank]",
            "description": "Add money to a user's account (Admin only: bankers can't create money)."
        },
        {
            "name": "clairvoyance report",
            "args": "",
            "description": "The State of the Realm: the week across every system (economy trend and where money came from, security incidents and verdicts, community activity, Cloudfall, Clairvoyance's health, birthdays ahead). Also posted every Sunday evening to the reports channel."
        },
        {
            "name": "clairvoyance status | reload",
            "args": "[version]",
            "description": "Admin: every Clairvoyance model in one view (Cloudfall, intent router, anomaly monitor, security models, language model), what it has learned from this server's verdicts, and the Cloudfall model's quality, latency and health; reload or roll back."
        },
        {
            "name": "remove-money",
            "args": "<user> <amount> [wallet|bank]",
            "description": "Remove money from a user's account (Admin only: bankers can't remove money)."
        },
        {
            "name": "set-income",
            "args": "<role> <amount> [hours]",
            "description": "Give a role an income, change it, or remove it with amount 0. Hours is how often it can be collected (1-720; kept when left out, 24 for a new income). Every change is posted to the audit channel."
        },
        {
            "name": "setup",
            "args": "[channels] [roles] [reset]",
            "description": "The channels (notifications, lottery, birthdays, audit, reviews, reports) and roles (admin, banker, bankrupt) Rudhabot uses here, in one place. Empty shows the current setup; changes apply at once. Changing the admin role needs the Administrator permission."
        },
        {
            "name": "lottery",
            "args": "",
            "description": "See each ticket's win chance, zonk chance, average return and jackpot, your tickets and recent jackpot winners."
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
        {
            "name": "warn",
            "args": "<member> <reason>",
            "description": "Warn a member: a numbered case in the mod log, and a DM to them. Helpers and above."
        },
        {
            "name": "timeout",
            "args": "<member> <minutes> <reason>",
            "description": "Time a member out. Helpers up to 10 minutes; Moderators up to 28 days."
        },
        {
            "name": "untimeout",
            "args": "<member> [reason]",
            "description": "Lift a timeout early."
        },
        {
            "name": "kick",
            "args": "<member> <reason>",
            "description": "Kick a member (Moderator). They get a DM with the reason."
        },
        {
            "name": "ban",
            "args": "<user> <reason> [delete_hours]",
            "description": "Ban anyone, member or not (Senior Moderator), optionally deleting their recent messages."
        },
        {
            "name": "massban",
            "args": "<user_ids> <reason> [delete_hours]",
            "description": "Ban up to 200 accounts by id in one go, paced for Discord's limits (Senior Moderator). Raid clean-up through this doesn't count against anyone's anti-nuke budget."
        },
        {
            "name": "unban",
            "args": "<user_id> <reason>",
            "description": "Lift a ban (Senior Moderator)."
        },
        {
            "name": "purge",
            "args": "<count> [member] [reason]",
            "description": "Delete recent messages here, optionally only one member's (Helpers up to 50, Moderators up to 500)."
        },
        {
            "name": "slowmode",
            "args": "<seconds> [channel] [minutes]",
            "description": "Set slowmode, optionally lifting it automatically after some minutes."
        },
        {
            "name": "case view | edit | delete",
            "args": "<number>",
            "description": "Look up a moderation case, correct its reason (Moderator) or remove it (Admin)."
        },
        {
            "name": "modlog",
            "args": "<user>",
            "description": "A member's file: standing (new, regular, established or trusted, and why), moderation cases, security incidents, economy anomaly signals and any economy hold."
        },
        {
            "name": "Report to moderators",
            "args": "",
            "description": "Right-click a message, then Apps. Flags it for staff without telling the member who reported it. Anyone can use it."
        },
        {
            "name": "security status",
            "args": "",
            "description": "Threat level, open and recent incidents, lockdowns, who is on probation or quarantined."
        },
        {
            "name": "security incident",
            "args": "<id> [details|report|redact]",
            "description": "One incident's evidence and actions; its after-action report; or delete the message snippets kept as evidence."
        },
        {
            "name": "security lockdown",
            "args": "<reason> [channel or category] [minutes]",
            "description": "Lock a channel, a category or (empty) the whole server. Shows a preview; another senior staff member approves; it lifts itself."
        },
        {
            "name": "security unlock",
            "args": "[lockdown]",
            "description": "End a lockdown now and put the permissions back."
        },
        {
            "name": "security level",
            "args": "<1-5> [reason] [hold_minutes]",
            "description": "Raise or lower the threat level by hand (Senior Moderator)."
        },
        {
            "name": "security release",
            "args": "<member>",
            "description": "Free a member from probation (Helper) or quarantine (Admin)."
        },
        {
            "name": "security ask",
            "args": "<question>",
            "description": "Ask about the security state in plain words (read-only)."
        },
        {
            "name": "security setup",
            "args": "[channels] [roles] [mode]",
            "description": "Alert channel, mod log, probation/verified/quarantine roles, and shadow or live mode (Admin). Empty shows the current setup."
        },
        {
            "name": "security staff",
            "args": "[role] [tier] [member]",
            "description": "See which roles are Helper, Moderator, Senior Moderator or Admin; set a role's tier (Admin); or check a member."
        },
        {
            "name": "security settings",
            "args": "[key] [value]",
            "description": "Detection settings: list what's changed, explain one, or change it (Admin). Versioned: key rollback restores an older version."
        },
        {
            "name": "security exempt",
            "args": "[member|role|channel|domain] [value] [days]",
            "description": "Exempt something from checks for a while (Senior Moderator); days 0 removes it; empty lists them."
        },
        {
            "name": "security tools",
            "args": "<action>",
            "description": "Health check of permissions and settings, attack drills in a sandbox, snapshots and restore, AutoMod rules, the verification panel, model status."
        },
    ]

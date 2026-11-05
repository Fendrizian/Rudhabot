# Rudhabot Guidebook

Rudhabot is the Rudharta server's own bot: an economy with a bank and markets, games, the Cloudfall card game, community tools, and the server's security. Part 1 is for every member; Part 2 is for staff.

<!-- The text /guide shows (rudhabot/ui/guide.py). Written for Discord: no tables; `## Part` and `### Chapter`
     headings; a chapter for staff only names its requirement in braces: {staff}, {banker}, {helper}, {admin}. -->

## Part 1: For every member

### Getting started

Type `/help` to see what you can do, and `/ask` (or @mention Rudhabot) when you're not sure which command you need. Read this guide any time with `/guide`.

- **`/help`** lists only the commands you can use, grouped into sections: Economy, Bank, Shop & Café, Markets, Gambling games, Cloudfall, Lottery & birthdays. Pick a section from the menu, or press **Full list**.
- **`/ask`** understands plain words. "Show my inventory" or "how do loans work" either offers the right command with a button, or answers from Rudhabot's own documentation. For a command that needs details, it shows the command to type, filled in from what you said ("flip a coin for 100 on heads" gives `/coinflip bet:100 choice:heads`); it never bets or sends anything for you. Answers are private unless you set `private:False`.
- **@Rudhabot** in a channel works like `/ask`, and replies in the channel.
- Most results are visible to the channel. Anything about only you, such as your balance tips or a command that failed, is sent privately.

The currency is the **ra**. Everyone starts with an empty wallet and bank.

### Earning money

The steady earners are `/daily`, `/work` and your role incomes; `/crime` and `/rob` pay more but can cost you.

- **`/daily`**: 50 ra, every 24 hours.
- **`/work`**: 10-30 ra, every 20 minutes.
- **`/crime`**: a 70% chance of 30-50 ra; otherwise a fine of 5-20% of your wallet + bank. Every 30 minutes.
- **`/rob <member>`**: a 50% chance to steal 50-75% of their wallet (they need at least 50 ra in it); otherwise a fine of 10-25% of your wallet + bank. Every 15 minutes. They're pinged either way.
- **`/collect`**: the income of each of your roles that has one, each role on its own cooldown (usually a day).

More:
- **Role incomes:** `/income` lists every role that earns money, how much and how often, with yours marked and when each is ready. `/collect` pays all your ready ones at once, into your wallet. Collecting late doesn't save up missed days.
- **Fines** come out of your wallet first, then your bank.
- **`/send <member> <amount|all>`** gives money from your wallet to someone else.
- **`/leaderboard`** ranks the server by wallet, bank or total.

### Bank, cards and loans

Money in the bank earns daily interest and is safe from `/rob`; your card tier sets how much interest, how much you can move each month, and your credit limit.

- **Red**: 0.05% interest a day, deposit and withdraw 10,000 each a month, credit limit 2,000. Everyone starts here.
- **Silver**: 0.10% a day, 20,000 a month, credit 5,000. Upgrade at a net worth of 25,000, for a 2,500 fee.
- **Gold**: 0.20% a day, 50,000 a month, credit 15,000. Upgrade at 100,000, fee 10,000.
- **Platinum**: 0.30% a day, 100,000 a month, credit 40,000. Upgrade at 300,000, fee 30,000.

How it works:
- **Moving money:** `/deposit` (wallet to bank) and `/withdraw` (bank to wallet), each within your monthly limit. `all` moves as much as the limit allows.
- **Your account:** `/balance` shows wallet, bank, total and card, plus your monthly limits and credit card. It may add a private tip from Clairvoyance, such as a statement falling due. `/bank history` lists your recent money movements, and `/bank tiers` compares the cards.
- **Upgrading:** `/bank upgrade` moves you to the next card once your net worth (wallet + bank) is high enough.
- **Credit card:** shop, café, card packs and wishes can go on the card. Each month's balance becomes a statement due on the 11th. An unpaid statement adds 5% interest once and freezes the card until it's paid. Nothing is ever taken automatically. See it with `/bank card info` and pay with `/bank card pay`.
- **Loans:** `/loan <amount> <days>` borrows 100-10,000 ra for 1-30 days at 2% a day. Check it with `/loaninfo` and repay with `/pay`. `/extend` adds days once, for a 10% fee.
- **Borrowed money stays put until it's repaid:** while you have a loan or a credit-card balance, `/send` (and a robber) can only take what you hold beyond what you owe and your bank doesn't cover. Check what you can move with `/loaninfo`.
- **When a loan falls due,** it is paid from your wallet, then your bank. If that still isn't enough, your stocks and coins are sold to cover it. Only if everything together falls short do you go bankrupt: your wallet and bank are emptied, you get the bankrupt role, and you can't borrow for a while (longer for bigger debts). A banker can lift that early.
- **A frozen credit card** also blocks new loans until it's unfrozen.

### Markets

You can invest wallet money in two stocks and four coins, and follow everything you hold in `/portfolio`, with your profit or loss.

- **Stocks** (`/stocks market`, `/stocks quote`, `/stocks buy`, `/stocks sell`): NAS (the National Shop, listed at 100) and JOOT (Joot Café, listed at 80). Their prices follow what the server actually buys at the shop and the café, so busy shopping lifts them. One person's spending only counts fully up to 2,000 a day per company, so a single big spender can't pump a price.
- **Trading stocks:** a 0.5% fee, and big orders move the price against you. One order can trade at most 5% of a company's shares.
- **Crypto** (`/crypto market`, `/crypto buy`, `/crypto sell`): SCAY (Sachen's & Atty's), RDC (Royal Digital Currency), LICH (Lichtensvonium) and WNC (Warnacoin). Prices move every minute; SCAY is the steadiest and WNC the wildest. Buy with an amount in ra; sell an amount of coins, or `all`. A coin that sinks to 100 ra has a 1-for-10 reverse split: the price is multiplied by 10 and every holding divided by 10, so what you hold is worth the same.
- **Wallet only:** investments are always bought from your wallet, never on the credit card.

### Shop, café and items

The National Online Shop (`/shop`) and the Joot Café (`/cafe`) sell items you can keep, use or sell back.

- **Buying:** browse with `/shop` or `/cafe`, or buy directly with `/buy <item> [quantity]`. The **Paying with** button, or `pay_with`, picks your wallet or your credit card.
- **Café specials** change every month and are limited: each one can be bought once a month.
- **Selling:** `/sell <item>` returns shop items for half their price.
- **Your items:** `/inventory` lists what you own, with the short names `/use` and `/sell` accept. `/look <item>` shows an item's lore, price, resale value and what `/use` does.
- **Using items:** `/use` draws lottery tickets, opens mystery boxes, cracks fortune cookies and sets up birthday cakes, among others.

### Games of chance

Every game takes a bet from your wallet; while a game is in progress, your other money commands wait until it ends.

- **`/coinflip <bet> <heads|tails>`**: call the coin. Pays 2× your bet.
- **`/dice <bet> <guess>`**: guess the sum of two dice (2-12). Harder sums pay more.
- **`/rps <bet> <choice>`**: rock, paper or scissors against Rudhabot. A tie returns your bet.
- **`/slots <bet>`**: spin the reels. Pays by symbol combination; any pair pays 1.4×.
- **`/roulette`**: American roulette, with many bet types. Bets are taken as you place them; the wheel spins once. A table you leave unspun for 5 minutes refunds its bets.
- **`/highlow <bet>`**: guess whether the next card is higher or lower. Pays by the odds table; the same rank (a tie) loses. If you don't answer within 5 minutes, the likelier guess is made for you.
- **`/crash <bet>`**: cash out before the multiplier crashes. Pays the multiplier when you cash out. After 30 seconds you're cashed out automatically at the multiplier reached.
- **`/blackjack <bet>`**: beat the dealer. Standard blackjack. If you walk away for 5 minutes, your hands stand and the dealer plays them out.
- **`/baccarat <bet> <on>`**: back Player, Banker or Tie. Player 1:1, Banker 1:1 less 5%, Tie 8:1.
- **`/craps <bet> <play>`**: Pass Line or Don't Pass (1:1), or a one-roll Field bet (2:1 on a 2, 3:1 on a 12).

Games are for fun. Coin flips and rock-paper-scissors are even bets; every other game keeps a house edge, so over time the house wins. Your private `/balance` tip tells you when you're down on games this week.

### Cloudfall and Skyfall Wishes

*Asgrelia: Cloudfall* is the server's own trading card game: claim a free starter deck, learn against the AI, then duel other members for rating and ra.

1. **Start:** `/tcg starter <deck>` gives you one of six archetype starter decks. `/tcg rules` explains the game, and `/tcg card <name>` looks up any card.
2. **Practice:** `/tcg practice` plays against Clairvoyance, Rudhabot's own trained AI, with coaching hints. On normal and hard it plans its whole turn before it moves, and a hint shows the rest of the turn it has in mind for each suggested move. Practice is unrated and pays nothing.
3. **Duel:** `/tcg challenge <member>` starts a rated match in its own thread, with a live win-chance bar for spectators. You act through private menus that only offer legal moves.
4. **Grow your collection:** open packs with `/tcg pack` (250 ra for 5 cards, wallet or card), and build decks with `/tcg deck build`, `view` and `use`. `/tcg deck analyze` asks Clairvoyance's Deck Doctor how your deck fares against every archetype.

More:
- **Rewards:** a rated win pays 150 ra, for up to 3 rewarded wins a day and 2 against the same opponent. `/tcg ladder` shows the ratings.
- **Skyfall Wishes:** `/tcg wish banner` shows this week's featured banner, its rates and pity, and `/tcg wish pull` makes 1 or 10 wishes at 160 Cloud Shards each. A ★5 is guaranteed by the 75th wish (it gets likelier from wish 56) and a ★4 every 10 wishes.
- **Cloud Shards:** 80 a day with `/tcg wish daily`, 10 ra each from `/tcg wish shards`, or from winning matches. Copies beyond what a deck can hold turn into Vapor Dust, which crafts any card you choose with `/tcg wish craft`. `/tcg wish convergence` is the server-wide goal everyone fills together.
- **During a match:** `/tcg match` re-posts your board, `/tcg forfeit` concedes. Idle players get a safe default move after 10 minutes, and lose after 3 of those.
- **Leaving:** `/tcg forget-me` deletes your Cloudfall data.

### Lottery and birthdays

Lottery tickets are drawn the moment you use them, and your registered birthday earns you a gift and a shout-out every year.

- **Regular ticket:** 100 ra. Jackpot 1,000,000, a 1 in 50,000 chance. Any win: 36%.
- **Golden ticket:** 1,000 ra. Jackpot 100,000,000, a 1 in 500,000 chance. Any win: 36%.

More:
- **Lottery:** buy tickets with `/buy`, draw them with `/use` (any quantity), and see every ticket's chances, average return (about 80%) and recent jackpot winners with `/lottery`. Jackpots are announced in the lottery channel.
- **Birthdays:** `/birthday set <day> <month> [year]` registers yours. On the day (Rudhanese Time, GMT+8) you get 500 ra and an announcement. `/birthday view`, `/birthday upcoming` and `/birthday remove` do what they say.
- **Birthday cake:** a Special Birthday Cake from the café lets you schedule a celebration for someone else with `/use cake`.

### Staying safe

If something looks like a scam or spam, report it; Rudhabot also watches for raids and scam links on its own.

- **Report a message:** right-click it (or long-press on mobile), then **Apps → Report to moderators**. The moderators are told; the member you reported never sees your name. You can send 5 reports every 10 minutes.
- **Scam links** to fake Discord, Steam or gift sites are flagged to the moderators, and removed automatically once security runs in live mode. Never log in through a link someone sends you.
- **Probation:** during a raid, new accounts can be put on probation for a while. It lifts by itself, or a moderator releases you.
- **When money can't move:** an account security has on probation or in quarantine can't send or receive money, rob or be robbed, or take a loan. During a raid alert, accounts that joined in the last 24 hours can't transfer either. Rudhabot tells you why, and when it lifts. Earning, spending and games still work.

**What Rudhabot keeps about you:**
- every change to your money (the audit trail);
- a count of how many messages you post each day, never what they say;
- the text of a message only if it was flagged, at most 200 characters, deleted after 14 days.

When AI answers are on, `/ask` sends only your question and Rudhabot's public docs to OpenAI, plus your own Cloudfall data if the question is about your match. Nothing about other members is sent, and nothing is stored.

## Part 2: For staff

### Who sees which commands {staff}

Each staff member sees only the commands their rank can use, in `/help` (privately), in `/ask`, and in Discord's command list; every command also checks the rank again when it runs.

- **Helper** (a role mapped to tier 1 with `/security staff`, or Manage Messages): `/warn`, short `/timeout`, `/untimeout`, `/purge` up to 50, `/slowmode`, `/case view`, `/modlog`, `/security status`, `incident`, `release`, `ask`.
- **Moderator** (tier 2, or Kick or Timeout Members): the above, plus `/kick`, long timeouts, `/case edit`, `/security lockdown`, `unlock`, `tools` and the read-only set-up views.
- **Senior Moderator** (tier 3, or Ban Members): the above, plus `/ban`, `/massban`, `/unban`, `/security level`.
- **Admin** (the admin role, Administrator or Manage Server): everything, including `/setup`, `/set-income`, `/add-money`, `/clairvoyance` and changing security settings.
- **Banker** (the banker role; admins count too): `/audit`, `/review`, `/banker`.

**Discord's command list** hides staff commands from anyone without the matching Discord permission. A role that should see them without that permission needs an override in **Server Settings → Integrations → Rudhabot** (Rudhabot can't set these itself):
- **Banker role:** `/banker`, `/audit`, `/review`, unless it has Manage Server.
- **Admin role:** `/add-money`, `/remove-money`, `/setup`, `/set-income`, `/clairvoyance`, unless it has Administrator.
- **Helper roles:** `/warn`, `/timeout`, `/untimeout`, `/purge`, `/slowmode`, `/modlog`, `/case`, `/security`, unless they have Manage Messages.
- **Moderator roles:** the Helper commands plus `/kick`, unless they have Kick Members.
- **Senior Moderator roles:** the Moderator commands plus `/ban`, `/massban`, `/unban`, unless they have Ban Members.

`/security staff` with no options lists which roles hold which tier.

### Bankers {banker}

Bankers look after members' accounts and keep an eye on the economy, but can't create or remove money; that stays with admins.

- **`/audit [member]`** shows the latest money movements, for everyone or one member. Every change to anyone's money is in this trail, labelled with the command or job that caused it.
- **`/review list`** shows Clairvoyance's review signals: unusual economy activity worth a look, such as a sudden stream of transfers or an implausible winning streak. Each signal also shows the member's standing and security record.
- **`/review resolve <id>`** marks a signal *dismissed* (nothing wrong) or *confirmed* (needs action). Your verdicts teach the anomaly monitor this server's bar. A signal never changes anything by itself.
- **`/banker account <member>`** shows a member's whole bank picture privately: balances, card, monthly limits, loan, holds and bans.
- **`/banker card-hold <member> <reason>`** declines their credit card until `/banker card-release`.
- **`/banker loan-unban <member> <reason>`** lifts a bankruptcy loan ban early and removes the bankrupt role.

Every banker action is answered privately and posted to the audit channel with who did it and why.

### Moderators {helper}

Every moderation command records a numbered case in the mod-log channel, and none of them works on yourself or on anyone of your rank or higher.

- **`/warn <member> <reason>`** (Helper): the member gets a DM.
- **`/timeout <member> <minutes> <reason>`** (Helper up to 10 minutes; Moderator up to 28 days): `/untimeout` lifts it.
- **`/purge <count> [member]`** (Helper up to 50 messages; Moderator up to 500): in the current channel.
- **`/slowmode <seconds> [channel] [minutes]`** (Helper): this or another channel; can lift itself after some minutes.
- **`/kick <member> <reason>`** (Moderator).
- **`/ban <user> <reason>`** (Senior Moderator): works on people who already left; can delete their recent messages.
- **`/massban <ids> <reason>`** (Senior Moderator): up to 200 accounts at once, for raid clean-up; more than 10 established accounts need a second Senior Moderator.
- **`/unban <id> <reason>`** (Senior Moderator).
- **`/case view | edit | delete`** (view: Helper; edit: Moderator; delete: Admin): an edit keeps the old reason in the record.

**`/modlog <member>` is the member file:**
- **standing:** new, regular, established or trusted, with the reasons. It is built from time in the server, active days, cases and incidents, never from message content;
- **moderation cases** from the last 6 months;
- **security incidents** they were part of, with staff verdicts;
- **economy anomaly signals**;
- **any economy hold.**

Trusted members are never timed out or put on probation automatically: you get a proposal instead, because even established accounts get hacked.

**Member reports** (*Report to moderators*) arrive in the security alert channel as incidents, without the reporter's name.

### Security {helper}

Security runs by itself and reports to the alert channel; staff answer its alerts and proposals with buttons, and decide anything serious.

**Shadow and live mode.**
- Security starts in **shadow mode**: it detects and reports everything, and records what it *would* have done.
- An admin switches on the automatic responses with `/security setup mode:live`.
- Automatic responses are only the low-impact ones: deleting flagged messages, temporary slowmode, probation for risky joins, timeouts of at most 10 minutes for repeat spammers.
- The one exception is a compromised staff account. Going over its budget of high-impact actions in 10 minutes gets its dangerous roles removed and a 10-minute timeout.
- Bots ranked above Rudhabot, such as Wick, are trusted and never touched.

**Alerts.**
- Each alert shows what was observed, who and where, and what was done.
- **After-action report** writes up the incident.
- **Real** and **False alarm** record your verdict. Clairvoyance uses verdicts to set how confident its models must be on this server, and the health check flags detectors that are mostly false alarms.
- Weaker findings arrive in a digest every 10 minutes.

**Proposals** (longer timeouts, kicks, bans, pausing invites) come with **Approve** and **Decline** buttons:
- they expire after 20 minutes;
- they re-check the presser's rank;
- lockdowns and restores need two different staff members.

**Threat levels:**
1. **Normal:** obvious spam removed; alerts as usual.
2. **Elevated:** slowmode on surging channels, probation for new accounts that join, short timeouts for repeat spammers.
3. **High:** probation for every join, links from new members removed, staff asked to pause invites; brand-new accounts can't transfer money.
4. **Severe:** staff asked to approve a lockdown of the affected channels; anti-nuke budgets halved.
5. **Critical:** staff asked to approve a server-wide lockdown and pausing DMs and invites; set by staff only.

**Everyday commands:**
- `/security status`: the level, open incidents, lockdowns, who is restricted.
- `/security incident <id>`: details, a report, or `redact` to delete kept snippets.
- `/security lockdown <reason> [target] [minutes]`: preview, approval, then it lifts itself. `/security unlock` ends it early.
- `/security level`: set the level by hand.
- `/security release <member>`: frees someone from probation (Helper) or quarantine (Admin).
- `/security ask`: questions in plain words.

**Set-up** (Moderators see it, Admins change it): `/security setup`, `staff`, `settings`, `exempt`, and `tools`. `tools` covers the health check, attack drills in a sandbox, snapshots and restore, AutoMod rules, the verification panel, and model status.

### Admins {admin}

Admins set Rudhabot up for the server, are the only ones who can create money, and see everything Clairvoyance does.

- **`/setup`** shows the server's set-up in one place, and changes take effect at once:
  - channels: notifications, lottery, birthdays, audit, reviews, reports;
  - roles: admin, banker, bankrupt;
  - the security set-up, and a reminder about the Discord overrides.

  `reset:` puts a setting back to its default. Changing the admin role needs the Administrator permission. Admin and banker role changes are announced in the security alert channel.
- **`/set-income <role> <amount> [hours]`** gives a role an income members collect with `/collect`, or changes it; `amount:0` removes it.
  - Hours (1-720) sets how often it can be collected; left out, it stays as it was, or 24 for a new income.
  - Limits: up to 1,000,000 per collection, at most 25 income roles; @everyone and bot roles can't have one.
  - Every change is posted to the audit channel.
- **`/add-money`** and **`/remove-money`** change a member's wallet or bank directly. Like every money change, they're in the audit trail.
- **`/clairvoyance status`** shows every Clairvoyance model in one view: the Cloudfall AI, the `/ask` router, the anomaly monitor, the security models, and the language model, with their health and what they have learned from your staff's verdicts. **`/clairvoyance reload`** reloads or rolls back the Cloudfall model.
- **`/clairvoyance report`** shows the **State of the Realm**, which is also posted every Sunday from 20:00 Rudhanese Time to the reports channel. It covers:
  - the week's economy, and where money came from and went;
  - security incidents and verdicts;
  - community activity;
  - Cloudfall;
  - Clairvoyance's health;
  - birthdays ahead.
- **Security, Admin only:**
  - switching between shadow and live mode;
  - mapping staff roles to tiers;
  - changing detection settings;
  - restoring from a snapshot;
  - releasing quarantine.

  Every settings change is posted to the alert channel, flagged when it weakens the defences.

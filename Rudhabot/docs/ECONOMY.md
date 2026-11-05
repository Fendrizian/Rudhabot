# Rudhabot economy: audit trail, bank, exchange and casino

## Audit trail (every money change)

**Where it's stored:**
- Every change to anyone's money is appended to the ledger, one entry per change: the `ledger` table of
  `rudhabot.db`, indexed by member and by time (or `ledger.jsonl` with `RUDHABOT_STORAGE=json`).
- It is append-only: history is never rewritten.
- A torn last line after a crash is skipped.
- The file rotates at 50 MB, and old files are kept.

**What's tracked:** wallet, bank, credit-card balance, crypto holdings (`crypto:SYM`), shares (`shares:SYM`),
Cloud Shards and Vapor Dust.

**Why nothing can be missed:** money only lives in a few repositories, and each one reports every write at the moment it
happens:
- `UserRepository` diffs wallet, bank and crypto on `update`, `transaction` and `save_all`. That covers all of the
  existing commands (`/work`, `/daily`, `/crime`, `/rob`, `/send`, every gambling game, the shop, loans, the lottery,
  birthdays and admin money) without per-command code.
- The bank, the exchange and the gacha report their own balances.

**How each entry is labelled (most specific wins):**
1. An explicit reason from the code, e.g. `bank interest (1 day(s))` or `card purchase: 1 × cake`.
2. The interaction that caused it. Every slash command is labelled `/command` by the command tree, and every button or
   select is labelled e.g. `Blackjack (button)` by `ServiceView`. Subclasses get this automatically.
3. Otherwise the code location, e.g. `services/loans.py:LoanService.auto_collect`. This covers background jobs.

**Entry fields:**

| Field | Contents |
|---|---|
| `id`, `ts` | Entry id and timestamp |
| `user` | The account that changed |
| `account` | Which balance changed |
| `delta`, `after` | The change and the resulting balance |
| `reason` | The label, as above |
| `source` | `command`, `component` or `code` |
| `actor` | Who caused it, e.g. the sender of a `/send` |

**Discord:**
- Entries are posted to the audit channel in batched embeds: up to 20 per embed, every 15 s.
- The audit channel is the economy log channel (`1215240269377830943`); change it with `/setup audit_channel:` (or
  `RUDHABOT_AUDIT_CHANNEL_ID`).
  Loan notices are not economy logs: they stay in the notifications channel.
- Bankers and admins: `/audit [user]`. Players: `/bank history`, which shows only their own entries.

## Banker role

Members with the banker role (`banker_role_id`, 1566271529245999255) and all administrators can:
- read the economy: `/audit [user]`, `/review list | resolve` and `/banker account <user>` (a member's balances, card,
  monthly limits, loan, holds and bans, shown privately);
- put a member's credit card on hold with a reason (`/banker card-hold`) and lift it (`/banker card-release`). A held
  card is declined with that reason, and paying the card doesn't lift the hold;
- lift a bankruptcy loan ban early, which also removes the bankrupt role (`/banker loan-unban`).

Bankers can't create or remove money: `/add-money` and `/remove-money` stay admin-only. Every banker action is answered
privately and posted to the audit channel with who did it and why. The banker role can be changed with `/setup`
(the change is announced in the security alert channel).

## Role incomes (`/collect`, `/income`, `/set-income`)

Certain roles earn money that their holders collect with `/collect`:
- **`/income`** (everyone) lists every role income, biggest first: the role, the amount and how often. The viewer's
  own income roles are marked, with when each is ready.
- **`/collect`** pays every ready income for the roles the member holds right now, to the wallet. Each role has its
  own cooldown, counted from that member's last collection of that role. Missed periods don't pile up: collecting
  late just starts the next wait. If nothing is ready, it says how long each role has left.
- **`/set-income <role> <amount> [hours]`** (Admin only, like `/add-money`, because incomes create money):
  - gives a role an income, or changes it;
  - `amount:0` removes it;
  - `hours` is how often it can be collected (1-720). Left out, it stays as it was, or 24 for a new income;
  - limits: up to 1,000,000 per collection, at most 25 income roles; @everyone and bot roles can't have one;
  - every change is answered privately and posted to the audit channel with who made it (old → new).

Notes:
- Incomes of roles deleted from the server are dropped automatically.
- Payouts are audited as `/collect` and appear in the weekly report's money sources.
- Incomes live in `rudhabot.db` (`role_incomes`, `income_collections`). A collection is saved before the wallet is
  credited, so a crash can lose one payout but never pay twice.

## Security holds

Money can't move to or from an account security has contained, so a raid or alt account can't launder it:
- on probation or in quarantine: no `/send` either way, no `/rob` either way, no `/loan`;
- during a raid alert (threat level 3+), accounts that joined in the last 24 hours can't send or receive.

The member is told why and how it lifts. Holds are listed in the member file (`/modlog`). Spending, earning and
games are unaffected.

## Daily snapshots and the weekly State of the Realm

Just after each day ends, a snapshot records:
- the money supply (wallets plus banks) and how many accounts hold money;
- credit and loans owed;
- the day's wallet and bank movements and active accounts;
- where money came from and went: the net change per command or job, from the audit ledger.

Snapshots are kept for 400 days (`economy_daily`).

Every Sunday from 20:00 Rudhanese Time, the **State of the Realm** is posted to the reports channel. That channel
follows the review channel; move it with `/setup reports_channel:`. Admins can see it at any time with
`/clairvoyance report`. It shows:
- the week's economy trend and money sources and sinks;
- security;
- community activity;
- Cloudfall;
- Clairvoyance's health;
- birthdays ahead.

It is read-only.

## Bank

| Card | Bank interest | Deposit & withdraw limit | Credit limit | Upgrade |
|---|---|---|---|---|
| <:bankcard_red:1417887131510116483> Red | 0.05% / day | 10,000 each / month | 2,000 | everyone starts here |
| <:bankcard_silver:1417887151156236480> Silver | 0.10% / day | 20,000 | 5,000 | net worth 25,000, fee 2,500 |
| <:bankcard_gold:1417887174895861792> Gold | 0.20% / day | 50,000 | 15,000 | net worth 100,000, fee 10,000 |
| <:bankcard_platinum:1417887196500983950> Platinum | 0.30% / day | 100,000 | 40,000 | net worth 300,000, fee 30,000 |

**Monthly limits:**
- `/deposit` (wallet → bank) and `/withdraw` (bank → wallet) are each limited per calendar month, by your card tier.
- `/deposit all` moves as much as the limit allows.

**Bank interest:**
- The bank balance earns the card tier's daily interest. There is no separate savings account.
- Interest is paid once a day by a background job, compounding, on the balance at that moment. Fractions carry over so
  small balances still earn.
- Interest is only paid on up to 5× the tier's monthly limit, so the bank can't print money.
- After downtime, at most a week of missed interest is paid.
- The job is crash-safe: it records that today is paid for every account *before* paying anyone, so an interrupted run
  can at worst skip a day for some accounts and can never pay the same day twice.
- Money that was in the old savings accounts moves into the bank balance the first time the account is used. The audit
  log records it as `savings merged into bank`.

**Credit card:**
- Every purchase can go on the card: `pay_with:card` on `/buy`, `/tcg pack` and `/tcg wish shards`, and the
  **Paying with** toggle on the `/shop` and `/cafe` menus. The bank balance never pays for purchases.
- Investments (`/stocks buy`, `/crypto buy`) are wallet-only. Card purchases can be resold for half in cash, so an
  unpaid card balance is reserved in the wallet like a loan (see Loans): that cash can't be sent away.
- On the first access in a month, the balance becomes that month's statement, due on the 11th.
- If the statement is unpaid after its due date, 5% interest is added once and the card is frozen until the statement is
  paid. No money is ever taken automatically.
- `/bank card info` shows the card, and `/bank card pay` pays it.

**Net worth:** wallet + bank.

**Balance:** `/balance [user]` shows wallet, bank (with its daily interest rate), the total and the card tier. For yourself it also shows the
monthly limits and the credit card.

**Clairvoyance insights:** your own `/balance` (sent to you privately) and `/bank card info` may show one or two quiet
observations
computed only from your own ledger:
- your statement is due soon;
- your card is mostly used;
- you're spending about N× your usual weekly amount;
- you're down on games of chance this week;

They are advisory and never change, limit or charge anything.

## Loans (`/loan`, `/loaninfo`, `/pay`, `/extend`)

- **Terms:** 100–10,000 ra for 1–30 days at 2% a day, compounding (what's owed is the amount × 1.02 per day
  so far). `/extend` adds days once, for a fee of 10% of the amount
  borrowed, up to 30 days in all. One loan at a time; a frozen credit card or a bankruptcy ban blocks new loans.
- **Borrowed money stays put until it's repaid.** While a loan or a credit-card balance is open, the part of those
  debts the bank balance doesn't cover is reserved in the wallet: it can be spent (shop, games), but `/send` can only
  move what's above it, and a robbery can only take what's above it. `/loaninfo` shows the sendable amount. Without
  this, a member could borrow (or buy on the card and resell for half), send the money to another account and never
  repay.
- **When a loan falls due,** it is paid from the wallet, then the bank. If that isn't enough, every coin and share
  the member holds is sold first (at market, through the normal audited trades; share sales are split into orders
  no bigger than one order may be). Only if everything together falls short is the member made bankrupt: wallet and
  bank are emptied, the bankrupt role is given, and new loans are banned for debt / 200 days (at least 1). Notices
  in the notifications channel say what was sold. A banker can lift a ban early with `/banker loan-unban`.

## Crypto (`/crypto`)

- Four coins (SCAY, RDC, LICH, WNC) move every minute, each with its own volatility.
- Holdings are kept to 4 decimals, and a buy rounds the coins *down*, so a purchase never gets more than it paid for
  (a buy too small for 0.0001 coins is refused).
- **Reverse splits instead of a price floor:** when a coin would sink below 100 ra, it has a 1-for-10 reverse split:
  the price is multiplied by 10 and every holding is divided by 10, so each holding keeps its value and the coin
  keeps moving. (The old hard floor pinned a falling coin at 100, which made buying at the floor a free option.)

## Rudharta Exchange (`/stocks`)

Your shares appear together with your crypto in `/portfolio`, with profit or loss against what you paid.

Two listings: NAS (The National Shop, IPO 100) and JOOT (Joot Cafe, IPO 80). More can be added in `domain/stocks.py`.

**Revenue:**
- Every shop purchase is NAS revenue, and every café purchase (specials included) is JOOT revenue. Card purchases count.
- Selling items back to the shop is an NAS expense.
- Revenue is an exponentially-weighted daily rate with a 3-day half-life.

**Fairness rules:**
- Each person's purchases count fully up to 2,000 per company per day, and at 10% beyond that. Prices rise when many
  people buy, not when one whale does.
- A simulation shows this: 20 shoppers spending 50k a day in total pushed NAS's value to 118, while one whale spending the
  same 50k a day only reached 89.

**Value:** fundamental value is IPO × (0.35 + 0.65 × √(revenue / reference)), capped at 8× IPO. The 0.35 part is the
company's asset value, so value can't reach zero: there is no default.

**Price:**
- Every 15 minutes the price mean-reverts toward value, with a speed of 0.35 per day and daily volatility of 3%.
- There is a hard floor at 10% of the IPO.
- After an outage the market catches up, including revenue decay, so no stale prices.

**Trading:**
- Orders fill along an impact curve: the price moves by exp(±0.6 × shares / 2% of shares outstanding).
- You pay or receive the average price along that path, plus a 0.5% fee.
- One order trades at most 5% of the shares outstanding.
- Buying and then selling immediately always loses the fee and the spread, so pump-and-dump doesn't pay. In tests, a
  round trip of 20,000 shares lost about 1%.

## Games of chance

Returns are what a bet pays back on average over many plays (100% = a fair game). Each is computed from the game's
rules and checked in `tests/test_exploits.py`, `tests/test_slots.py` and the simulations noted.

| Game | Return to player | How |
|---|---|---|
| `/coinflip` | 100% (fair) | even money on a fair coin |
| `/rps` | 100% (fair) | a win pays even money, a tie returns the bet |
| `/dice` | 17%–50% | the sum of two dice; 7 pays ×3 (50%), 2 or 12 pay ×6 (17%) |
| `/slots` | 95.1% (exactly 475,589 / 500,000) | the symbol table; any pair pays ×1.4 |
| `/roulette` | 94.7% (Five Numbers 92.1%) | American wheel (0 and 00); 0 and 00 lose every outside bet |
| `/highlow` | 94.8%–95.4% | the odds table; the same rank (a tie) loses |
| `/crash` | 95% at every cash-out point | the crash point is drawn up front: P(above m) = 0.95 / m |
| `/blackjack` | standard rules (not measured) | blackjack pays 3:2, the dealer stands on 17, double down and split |
| `/baccarat` | Player 98.8% · Banker 98.9% · Tie 85.6% | 8-deck punto banco, full third-card tableau |
| `/craps` | Pass 98.6% · Don't Pass 98.6% · Field 97.2% | Field: 2 pays 2:1, 12 pays 3:1; Don't Pass pushes on 12 |

Baccarat and craps were also checked by 400,000-hand simulations (within 0.35 points of the theory).

**Walking away never refunds a game in progress.** Every game settles by its own rules:
- `/blackjack`: after 5 minutes without a move, the remaining hands stand and the dealer plays them out.
- `/highlow`: after 5 minutes, the likelier guess is made.
- `/crash`: a round lasts 30 seconds; if it hasn't crashed by then, you're cashed out at the multiplier reached (the
  same 95% as any other cash-out point).
- `/roulette`: the wheel spins once; a second press of SPIN, or a bet form submitted after it, is refused and charges
  nothing. A table nobody spins for 5 minutes refunds its bets, which is fair because no number has been drawn.
- Buttons that change a bet (double down, split) are checked against the hand as it is now, so a double click is
  refused instead of charged twice.

**While a bet is open** (any game), money can't be spent elsewhere: every money command, `/tcg pack`,
`/tcg wish shards` and the `/shop` and `/cafe` buttons refuse with "Finish your current game first". Games settle
against the wallet as it is at the end, never below zero, so spending a bet mid-game would have let a lost bet go
uncollected. A game always releases the player lock first and updates its message afterwards, so a message that
can't be edited (deleted, Discord outage) can never leave a player locked out.

**Fines** (`/crime`, a failed `/rob`) are sized on wallet + bank and taken from the wallet first, then the bank; a
balance never goes below zero. Money can't be sent to (or robbed from) bots.

## Review signals (Clairvoyance anomaly detection)

See `docs/ML.md`. Signals go to the review channel (`/setup reviews_channel:` or `RUDHABOT_REVIEW_CHANNEL_ID`, which
defaults to the audit channel). Bankers and admins see them with `/review list` and close them with `/review resolve`.
Nothing is ever changed automatically.
- Each posted signal shows the member's standing and security record.
- Verdicts teach the monitor this server's bar (within fixed bounds).

Each member is judged at most once a minute; activity that arrives inside that minute is picked up by a sweep every
15 seconds, so a burst that starts and ends within a minute is still looked at.

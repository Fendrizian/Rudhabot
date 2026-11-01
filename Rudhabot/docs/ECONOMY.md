# Rudhabot economy: audit trail, bank, exchange and casino

## Audit trail (every money change)

**Where it's stored:**
- Every change to anyone's money is appended to `ledger.jsonl`, one JSON line per change.
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
- The audit channel is the economy log channel (`1215240269377830943`); override it with `RUDHABOT_AUDIT_CHANNEL_ID`.
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
privately and posted to the audit channel with who did it and why.

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
- Investments (`/stocks buy`, `/buycoin`) are wallet-only, so the card can't be turned into cash.
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

## Casino additions

| Game | Bets | Payouts | House edge (theory / 400k-hand simulation) |
|---|---|---|---|
| `/baccarat` | Player · Banker · Tie | 1:1 · 1:1 minus 5% · 8:1 (Player/Banker push on a tie) | 1.24% / 1.03% · 1.06% / 1.26% · 14.4% / 14.7% |
| `/craps` | Pass Line · Don't Pass · Field | 1:1 · 1:1 (12 pushes) · 1:1, 2 pays 2:1, 12 pays 3:1 | 1.41% / 1.36% · 1.36% / 1.33% · 2.78% / 2.45% |

Baccarat uses an 8-deck shoe and the full punto banco third-card tableau. Both games use the same bet validation and
player lock as the other games, and their results are audited like every other money change.

**While a bet is open** (any game), money can't be spent elsewhere: every money command, `/tcg pack`,
`/tcg wish shards` and the `/shop` and `/cafe` buttons refuse with "Finish your current game first". Games settle
against the wallet as it is at the end, never below zero, so spending a bet mid-game would have let a lost bet go
uncollected. A game always releases the player lock first and updates its message afterwards, so a message that
can't be edited (deleted, Discord outage) can never leave a player locked out.

**Fines** (`/crime`, a failed `/rob`) are sized on wallet + bank and taken from the wallet first, then the bank; a
balance never goes below zero. Money can't be sent to (or robbed from) bots.

## Review signals (Clairvoyance anomaly detection)

See `docs/ML.md`. Signals go to the review channel (`RUDHABOT_REVIEW_CHANNEL_ID`, which defaults to the audit channel).
Bankers and admins see them with `/review list` and close them with `/review resolve`. Nothing is ever changed
automatically.

Each member is judged at most once a minute; activity that arrives inside that minute is picked up by a sweep every
15 seconds, so a burst that starts and ends within a minute is still looked at.

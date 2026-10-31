# Rudhabot economy: audit trail, bank, exchange and casino

## Audit trail (every money change)

**Where it's stored:**
- Every change to anyone's money is appended to `ledger.jsonl`, one JSON line per change.
- It is append-only: history is never rewritten.
- A torn last line after a crash is skipped.
- The file rotates at 50 MB, and old files are kept.

**What's tracked:** wallet, bank, savings, credit-card balance, crypto holdings (`crypto:SYM`), shares (`shares:SYM`),
Cloud Shards and Vapor Dust.

**Why nothing can be missed:** money only lives in a few repositories, and each one reports every write at the moment it
happens:
- `UserRepository` diffs wallet, bank and crypto on `update`, `transaction` and `save_all`. That covers all of the
  existing commands (`/work`, `/daily`, `/crime`, `/rob`, `/send`, every gambling game, the shop, loans, the lottery,
  birthdays and admin money) without per-command code.
- The bank, the exchange and the gacha report their own balances.

**How each entry is labelled (most specific wins):**
1. An explicit reason from the code, e.g. `savings interest (3 day(s))` or `card purchase: 1 × cake`.
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
- The audit channel is, for now, the notifications channel where the bank's loan notices go. Point it at a dedicated
  channel with `RUDHABOT_AUDIT_CHANNEL_ID`.
- Admins: `/audit [user]`. Players: `/bank history`, which shows only their own entries.

## Bank

| Card | Savings interest | Deposit & withdraw limit | Credit limit | Upgrade |
|---|---|---|---|---|
| <:bankcard_red:1417887131510116483> Red | 0.05% / day | 10,000 each / month | 2,000 | everyone starts here |
| <:bankcard_silver:1417887151156236480> Silver | 0.10% / day | 20,000 | 5,000 | net worth 25,000, fee 2,500 |
| <:bankcard_gold:1417887174895861792> Gold | 0.20% / day | 50,000 | 15,000 | net worth 100,000, fee 10,000 |
| <:bankcard_platinum:1417887196500983950> Platinum | 0.30% / day | 100,000 | 40,000 | net worth 300,000, fee 30,000 |

**Monthly limits:**
- `/deposit` (wallet → bank) and `/withdraw` (bank → wallet) are each limited per calendar month, by your card tier.
- `/deposit all` moves as much as the limit allows.

**Savings:**
- `/bank save` moves money from your bank balance into savings, and `/bank unsave` moves it back, once every 24 h.
- Interest is daily and compounding, and fractions carry over so small balances still earn.
- Interest is only paid on up to 5× the tier's monthly limit, so savings can't print money.
- Interest is applied whenever the account is touched, with no background job.

**Credit card:**
- Pay with `pay_with:card` on `/buy` and on `/tcg wish shards`.
- On the first access in a month, the balance becomes that month's statement, due on the 11th.
- If the statement is unpaid after its due date, 5% interest is added once and the card is frozen until the statement is
  paid. No money is ever taken automatically.
- `/bank card info` shows the card, and `/bank card pay` pays it.

**Net worth:** wallet + bank + savings.

**Clairvoyance insights:** `/bank account` and `/bank card info` are private, and may show one or two quiet observations
computed only from your own ledger:
- your statement is due soon;
- your card is mostly used;
- you're spending about N× your usual weekly amount;
- you're down on games of chance this week;
- idle bank money could be earning savings interest.

They are advisory and never change, limit or charge anything.

## Rudharta Exchange (`/stocks`)

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

## Review signals (Clairvoyance anomaly detection)

See `docs/ML.md`. Signals go to the review channel (`RUDHABOT_REVIEW_CHANNEL_ID`, which defaults to the audit channel).
Admins see them with `/review list` and close them with `/review resolve`. Nothing is ever changed automatically.

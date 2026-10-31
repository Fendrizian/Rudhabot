# Rudhabot

A Discord economy / gambling bot (discord.py 2.x).

## Run

```bash
export DISCORD_TOKEN='your-bot-token'      # the token is no longer stored in the code
python main.py
```

Data files (`data.json`, `crypto.json`, `specials.json`, `birthdays.json`, plus the new `birthday_registry.json` and
`lottery.json`) are created in the working directory, exactly where the old bot kept them. Existing files keep working.

## Test

```bash
python -m unittest discover -s tests -t .      # 115 tests, standard library only
```

## Architecture

Each layer only depends on the ones above it.

| Layer | What lives there |
|---|---|
| `settings`, `errors`, `clock` | Immutable config dataclasses, shared exceptions, an injectable clock |
| `domain/` | Pure rules, no Discord and no files: `SlotMachine`, `Lottery`/`PrizeTable`, `MysteryBox`, `Loan`, `BirthdayDate`, blackjack, `ItemCatalog`, `Cooldown`, amount parsing |
| `persistence/` | `JsonStore` (atomic writes, corrupt files are backed up) and repositories: `UserRepository`, `SpecialStockRepository`, `PriceBook`, `LotteryLedger`, `BirthdayRegistry`, `CelebrationLog` |
| `services/` | `LoanService`, `CryptoMarket`, `ShopService`/`CafeMenu`, `LotteryService`, `BirthdayService`, `GamblingSessions`, `AdminPolicy` |
| `ui/`, `views/` | Embeds, `Messenger`, `LoanNotifier`, `Announcer`, buttons/selects/modals (dependencies injected, never global) |
| `cogs/` | One cog per feature area. Commands are thin: parse input, call a service, render |
| `container.py`, `app.py` | Composition root: `Services.build()` wires everything; `RudhaBot` loads the cogs |

Time and randomness are injected (`Clock`, `rng`), so every rule can be tested deterministically.

## Lottery

Tickets are bought with `/buy` and drawn instantly with `/use` (any quantity). `/lottery` shows the live odds table.
Jackpots are announced in the lottery channel.

| | Regular | Golden |
|---|---|---|
| Price | 100 | 1,000 |
| Smallest prize | 10 | 100 |
| Jackpot | 1,000,000 | 100,000,000 |
| Jackpot chance | 0.002% (1 in 50,000) | 0.0002% (1 in 500,000) |
| Zonk / any win | 64% / 36% | 64% / 36% |
| Return to player | ~80% | ~80% |

Probabilities are exact integer weights over 10,000,000 outcomes (`domain/lottery.py`). The constructor *refuses to load*
a table whose jackpot is not rarer than 0.005%, where a zonk is not more likely than a win, or where bigger prizes are not
strictly rarer.

## Birthdays

`/birthday set | view | remove | upcoming`. On your registered birthday (GMT+8) you get a gift
(`BirthdayTerms.gift_amount`, default 500) and an announcement. The Special Birthday Cake now stores its celebration on
disk, so a restart no longer loses it, and failed deliveries are retried.

## Tunables (`settings.py`)

Paths, channel IDs, loan terms, birthday gift/timezone, admin role. `Channels.birthdays` defaults to the notifications
channel because the original code pointed at a placeholder ID that could never resolve; set it to your real channel.

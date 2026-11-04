# Rudhabot v2

A Discord economy and games bot (discord.py 2.x) built around **its own intelligence layer**:

- **Clairvoyance** is Rudhabot's in-house ML, with no hosted model involved. It does three things:
  - a slot-encoder value network for the card game (AI opponent, coaching, live win chance, Deck Doctor, similar cards,
    gentle finance tips);
  - an intent router that turns plain words ("show my inventory") into the right command;
  - an anomaly monitor that raises review signals for moderators.

  See `docs/ML.md`.
- **Asgrelia: Cloudfall** is a full trading card game built from the TCG Codex, with rated duels, a ladder and the
  **Skyfall Wishes** gacha. See `docs/TCG.md`.
- **The economy** has an audit log of every money change, a bank with card tiers, daily interest and credit cards, the
  **Rudharta Exchange** (NAS and JOOT stocks driven by real shop activity) and **Baccarat** and **Craps**. See
  `docs/ECONOMY.md`.
- **`/ask`** (or an @mention) routes commands locally, and answers questions with OpenAI when it's configured. See
  `docs/OPENAI.md`.
- **Server security:**
  - raid, spam and phishing defence; anti-nuke budgets with automatic quarantine of a compromised staff account;
  - five threat levels with standing orders;
  - scoped lockdowns with two-person approval and safe restore;
  - server snapshots, a numbered moderation log, incident reports and sandboxed attack drills.

  Clairvoyance adds an 8.4M-parameter message-risk model, an incident model and an alert ranker, all advisory. See
  `docs/SECURITY.md`.
- **One server, one system:**
  - **Member standing** ties security, moderation and the economy together. Trusted members are spared automatic
    restrictions; contained accounts can't move money.
  - `/modlog` is a member file.
  - `/setup` holds the server's channels and roles in one place.
  - Clairvoyance learns this server's bar from staff verdicts.
  - A weekly **State of the Realm** report.

  See `CHANGELOG.md`.
- Everything from v1 is still here.

## Run

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env        # then fill in DISCORD_TOKEN and, optionally, OPENAI_API_KEY
```

```bash
python main.py
```

`main.py` loads `.env` (next to it, or in the working directory). Variables already exported in your shell win over the
file. The startup log says whether the OpenAI assistant is enabled, and if not, why.

Check the OpenAI setup with one real request:

```bash
python -m rudhabot.llm.check "What's the mass of the sun?"
```

Discord permissions the bot needs in the channels where people play Cloudfall:
- *View Channel*, *Send Messages*, *Embed Links* and *Read Message History*: for the live match board.
- *Create Public Threads* and *Send Messages in Threads*: so each match gets its own thread.

Without these permissions, matches still work. The board is attached to the command reply and refreshes whenever a player
presses **Act**.

### Production checklist

1. **Secrets:** `DISCORD_TOKEN` (required) and `OPENAI_API_KEY` (optional) in `.env`, which is gitignored. Every other
   setting in `.env.example` is filled in with the values the bot uses: channels, roles, limits, model settings.
2. **A clean economy:** `RUDHABOT_DATA_DIR=data` keeps every data file in `data/` next to `main.py`. A new, empty
   folder means the bot starts with no balances, no inventories and no audit history; every file is created on first
   use. (Unset, files go to the working directory, as before.)
3. **Discord:** in the Developer Portal enable the **Server Members** and **Message Content** intents (security and
   @mentions); keep the bot's role **above every staff role** and the bankrupt role (*Manage Roles*). Security needs
   *View Audit Log* for anti-nuke (`docs/SECURITY.md` lists every permission and what stops without it).
4. **Home server and set-up:**
   - set `RUDHABOT_GUILD_ID` to the server's id; commands then register there instantly, and other servers are
     refused;
   - check `/setup`: channels and roles, all changeable live.
5. **Security setup:** `/security setup alert_channel:... modlog_channel:... probation_role:...`, map staff roles with
   `/security staff`, run `/security tools action:health check`, rehearse with `/security tools action:drill`. Security starts in **shadow
   mode** (detect and report only) until an admin runs `/security setup mode:live`.
6. **Channels the bot must be able to post embeds in:** notifications (loan notices), audit (economy log, banker
   actions, review signals), lottery (jackpots), birthdays, reports (the weekly State of the Realm; defaults to the
   review channel).
7. **Storage:** everything lives in `data/rudhabot.db` (SQLite, built into Python: no server, no hosted database).
   It is backed up daily to `data/backups/` (14 kept). Existing JSON files are imported once on first start and
   left in place.
   - Check or copy it with `python -m rudhabot.persistence.database info|check|backup <dir>`.
   - `RUDHABOT_STORAGE=json` keeps the old one-file-per-store layout; `... export <dir>` writes the database back out
     as JSON.
8. **Model:** `clairvoyance-v3` ships in `models/` (and `oracle-v1` / `clairvoyance-v2` for rollback). The bot works
   without it (`CLAIRVOYANCE_ENABLED=0`): it falls back to a deterministic heuristic AI.

Data (inside `RUDHABOT_DATA_DIR`): `rudhabot.db` holds every store:
- accounts, the bank, the audit ledger, Cloudfall profiles and live matches (restored after a restart);
- stocks, crypto, the lottery, birthdays, the gacha meter, review signals;
- all security records.

Hot stores are kept in memory and written through on every change; with SQLite only the changed records are written,
in one transaction. A change made to the database by another process is noticed and reloaded. With
`RUDHABOT_STORAGE=json` the same data lives in `data.json`, `bank.json`, `ledger.jsonl`, and so on, as before.

## Test

```bash
python -m unittest discover -s tests -t .      # offline: no Discord connection, no OpenAI calls
```

Rebuild, recalibrate and benchmark the Cloudfall model (each about 25, 2 and 4 minutes on 4 cores):

```bash
python -m rudhabot.ml.train --version my-model --init clairvoyance-v3 --ema 0.999 --hide-hand 0.15 --no-current
```

```bash
python -m rudhabot.ml.recalibrate --version my-model
```

```bash
python -m rudhabot.ml.benchmark --versions clairvoyance-v3,my-model
```

Retrain and benchmark the security models (the public datasets are downloaded and checksum-verified; about 11
minutes on 4 cores):

```bash
python -m rudhabot.ml.security.train --data ~/rudhabot-datasets --download
```

Retrain the intent router and re-run the anomaly benchmark:

```bash
python -m rudhabot.ml.intent
```

```bash
python -m rudhabot.ml.anomaly --tune
```

## What's new in v2

| Area | Commands |
|---|---|
| Cloudfall | `/tcg starter`, `/tcg collection`, `/tcg pack`, `/tcg deck build/view/use/analyze`, `/tcg card`, `/tcg rules`, `/tcg practice`, `/tcg challenge`, `/tcg match`, `/tcg forfeit`, `/tcg ladder`, `/tcg forget-me` |
| Assistant | `/ask <question> [private]` |
| Bank | `/deposit`, `/withdraw` (monthly card limits), `/balance` (bank earns daily interest; card tier), `/bank tiers | history | upgrade`, `/bank card info | pay` |
| Markets | `/portfolio` (crypto + stocks), `/stocks market | quote | buy | sell`, `/crypto market | buy | sell` |
| Casino | `/baccarat`, `/craps` |
| Items | `/look <item>` (lore, price, aliases); `/inventory` lists each item's aliases for `/use` and `/sell` |
| Skyfall Wishes | `/tcg wish banner | pull | shards | daily | craft | convergence` |
| Banker | `/audit`, `/review list | resolve`, `/banker account | card-hold | card-release | loan-unban` |
| Admin | `/clairvoyance status | report | reload`, `/setup` |
| Moderation | `/warn`, `/timeout`, `/untimeout`, `/kick`, `/ban`, `/massban`, `/unban`, `/purge`, `/slowmode`, `/case view | edit | delete`, `/modlog` (the member file), the *Report to moderators* message command |
| Security | Everyday: `/security status | incident | lockdown | unlock | level | release | ask` · set-up: `/security setup | staff | settings | exempt | tools` |

Designed for Discord:
- Each duel gets its own **thread**, so channels stay readable.
- Spectators see a live board with a **spectator-safe win bar**: the estimate is computed with both hands hidden.
- Players act through **ephemeral menus of legal moves**. Duplicate or stale clicks are rejected, matches survive bot
  restarts, and idle players are auto-moved and then forfeited.
- Rated wins pay a small, capped reward in ra. Practice is unrated, so the AI can't be farmed.

## Architecture

Each layer only depends on the ones above it.

| Layer | What lives there |
|---|---|
| `settings`, `errors`, `clock` | Immutable config dataclasses (`AISettings.from_env()` for the AI layer), shared exceptions, an injectable clock |
| `domain/`, `tcg/` | Pure rules, with no Discord and no files. v1 games, plus the Cloudfall engine (`tcg/engine.py`, deterministic and replayable) |
| `ml/` | The Clairvoyance: features, numpy network, policy and search, self-play, training, evaluation, versioned artifact store; `ml/security/` holds the wargame simulator and the security models |
| `security/` | Server security without Discord: events, detectors, anti-nuke, threat levels, planning, lockdown/snapshot planning, the action adapter (`discord_port.py` is the only part that talks to Discord) |
| `llm/` | Provider interface (`LanguageModel`) and the OpenAI Responses client, local retrieval, rate limits, the assistant |
| `persistence/` | SQLite storage (`database.py`) and the JSON fallback (`JsonStore`), repositories, security records |
| `services/` | v1 services, plus `ClairvoyanceService` (threaded, bounded, timed out, with fallback) and `TcgService` (match lifecycle) |
| `ui/`, `views/` | Embeds and components (`MatchButton` dynamic items survive restarts) |
| `cogs/` | Thin slash commands, one cog per area |
| `container.py`, `app.py` | Composition root |

Trust boundary: the ML and the LLM only advise. Every game move goes through `engine.apply`, which re-validates it. Balances,
rewards, ratings and permissions are decided only by deterministic code.

## Changes to existing behaviour

- `/help` lists the new commands. The help text for `/daily` claimed 200ra; it now matches the actual payout of 50ra (the
  payout itself is unchanged).
- Fines from `/crime` and a failed `/rob` come from the wallet first and then the bank; they used to come from the
  wallet alone and could leave it negative. Money can't be sent to (or robbed from) bots.
- While a bet is open, `/tcg pack`, `/tcg wish shards` and the `/shop` / `/cafe` buttons are refused like every other
  money command (spending a bet mid-game let a lost bet go uncollected).
- Amounts accept thousands separators: `1,000` works wherever a number does.
- Looking someone up (`/balance @user`, `/bank`) no longer creates an empty account on disk.

## Lottery

Tickets are bought with `/buy` and drawn instantly with `/use` (any quantity). `/lottery` shows each ticket's win and zonk chance, average return and jackpot (no per-prize odds).
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

Paths, channel IDs, loan terms, birthday gift/timezone, admin, banker and bankrupt roles, the home server. Every id can
be overridden from `.env` (see `.env.example`), and the channels and roles also live with `/setup`, which wins over
both.

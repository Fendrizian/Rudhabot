# Rudhabot v2

A Discord economy and games bot (discord.py 2.x) built around **its own intelligence layer**:

- **The Oracle** is an in-house neural model, trained by Rudhabot on self-play of its own card game. It powers the AI
  opponent, coaching hints, a live win-chance bar, post-match turning points, Deck Doctor and card similarity. See `docs/ML.md`.
- **Asgrelia: Cloudfall** is a full trading card game built from the TCG Codex: 112 cards, 8 Protocols and 4 Skies, with
  rated duels in threads and a ladder. See `docs/TCG.md`.
- **`/ask`** is an assistant grounded in Rudhabot's own docs. It uses OpenAI when configured and local retrieval
  otherwise. See `docs/OPENAI.md`.
- Everything from v1 is still here: economy, shop and café, loans, crypto, gambling games, the lottery and birthdays.

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

Data files are created in the working directory, exactly where the old bot kept them:
- existing: `data.json`, `crypto.json`, `specials.json`, `birthdays.json`, `birthday_registry.json`, `lottery.json`;
- new: `tcg.json` (Cloudfall profiles) and `tcg_matches.json` (live matches, restored after a restart).

Existing files are read unchanged. No migration is needed, and nothing in the old files is rewritten.

The Oracle model ships in `models/` (`oracle-v1`, 173 KB). The bot works without it (`ORACLE_ENABLED=0`): it falls back to a
deterministic heuristic AI.

## Test

```bash
python -m unittest discover -s tests -t .      # offline: no Discord connection, no OpenAI calls
```

Rebuild or evaluate the model:

```bash
python -m rudhabot.ml.train --version oracle-v2     # ~7 minutes on 4 cores
```

```bash
python -m rudhabot.ml.evaluate
```

## What's new in v2

| Area | Commands |
|---|---|
| Cloudfall | `/tcg starter`, `/tcg collection`, `/tcg pack`, `/tcg deck build/view/use/analyze`, `/tcg card`, `/tcg rules`, `/tcg practice`, `/tcg challenge`, `/tcg match`, `/tcg forfeit`, `/tcg ladder`, `/tcg forget-me` |
| Assistant | `/ask <question> [private]` |
| Model ops (admin) | `/oracle status`, `/oracle reload [version]` |

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
| `ml/` | The Oracle: features, numpy network, policy and search, self-play, training, evaluation, versioned artifact store |
| `llm/` | Provider interface (`LanguageModel`) and the OpenAI Responses client, local retrieval, rate limits, the assistant |
| `persistence/` | `JsonStore` (atomic writes; corrupt files are backed up) and repositories, including `TcgRepository` |
| `services/` | v1 services, plus `OracleService` (threaded, bounded, timed out, with fallback) and `TcgService` (match lifecycle) |
| `ui/`, `views/` | Embeds and components (`MatchButton` dynamic items survive restarts) |
| `cogs/` | Thin slash commands, one cog per area |
| `container.py`, `app.py` | Composition root |

Trust boundary: the ML and the LLM only advise. Every game move goes through `engine.apply`, which re-validates it. Balances,
rewards, ratings and permissions are decided only by deterministic code.

## Changes to existing behaviour

- `/help` lists the new commands. The help text for `/daily` claimed 200ra; it now matches the actual payout of 50ra (the
  payout itself is unchanged).

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

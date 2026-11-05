# Changelog

## Final checkup, refactor and Clairvoyance upgrade (November 2026)

### Exploits closed
- **Mentions:** the bot never pings @everyone or roles. It has Administrator, and display names reach some message
  text, so a nickname of "@everyone" could have pinged the whole server. Only members are ever pinged.
- **Casino odds** (the bigger house edge chosen):
  - slots returned 124.8%: a pair now pays ×1.4 (95.1%);
  - crash returned up to 114.9%: the crash point is drawn up front, so every cash-out point returns 95%;
  - high-low returned up to 103%: a tie (same rank) now loses (94.8–95.4%).

  Coin flip and rock-paper-scissors stay even bets. Every game's return is in `docs/ECONOMY.md`.
- **Roulette paid one stake twice** when SPIN was pressed twice quickly (about 189% return). The first press now
  closes the table. A bet form submitted after the spin is refused and charges nothing; it used to be taken and never
  settled.
- **Walking away is never a refund:**
  - blackjack hands stand and settle after 5 minutes;
  - high-low makes the likelier guess;
  - a crash round cashes out at the multiplier reached after 30 seconds;
  - an unspun roulette table still refunds (no number was drawn).

  A double click on Double Down or Split is refused instead of charging twice.
- **Loans couldn't be laundered and defaulted on any more:**
  - `/send` and `/rob` can't touch the wallet money a loan needs;
  - `/loaninfo` shows what can be sent;
  - an overdue loan sells crypto and shares before bankruptcy;
  - a frozen card blocks new loans.
- **Credit cards couldn't be cashed out to another account any more:** card purchases resell for half in cash, so
  every new account could turn its credit limit into cash for a main account and never pay. An unpaid card balance
  now counts like a loan: the wallet money it needs (beyond what the bank covers) can't be sent or robbed.
- **Crypto rounding:** a buy rounded the coins it got *up* to 4 decimals, so many small buys at a high price added up
  to free coins. Buys now round down.
- **Birthday gift:** removing and re-adding a birthday no longer pays the gift again that year. Only the year of
  the last gift is kept after a removal (never the date), and only until that year ends.
- **Crypto:** a coin sinking to 100 ra now has a value-preserving 1-for-10 reverse split instead of sitting on a floor
  it could only rise from (buying there was risk-free).

### Fixes
- `/security release` defers before restoring roles, which could take longer than 3 seconds.
- One storage error can no longer stop the loan-warning loop.
- Money changes from submitted forms are labelled in the audit trail.

### Refactor (no behaviour change)
- **Security service:** split into focused mixins: moderation, verdict learning, recovery.
- **`/help`:** its data moved to `views/help_data.py`.
- **Source hygiene:**
  - no invisible characters in the source;
  - dead code and unused parameters removed;
  - pyflakes is clean across the bot and the tests.

### Clairvoyance: better reasoning, new models
- **Cloudfall plans its turn.** For the five most promising moves, the AI plays out the rest of its own turn and
  judges each move by the whole line. It never assumes the opponent's replies, and sees hidden cards only through
  determinization.
  - With the same v3 network, it beats the old one-step AI **62.2%** (95% interval 58.2–66.0%) over 600
    seat-swapped games, at about 10 ms a move.
  - Normal and hard seats use it; easy stays one step ahead.
  - **Coach hints now show the plan** ("then: Go to the Clash → Attack with …"), cut before anything that depends on
    cards the AI had to guess.
- **`clairvoyance-v4`** is served. It was trained on 18,000 games played by the planning AI, so it learned from
  stronger play and from whole planned lines. On fresh seeds, with the same lookahead:
  - it beats v3 head to head **55.8%** (51.8–59.8%);
  - it beats the heuristic 74.9% (v3: 72.0%);
  - it predicts the AI's own positions better (log-loss 0.516 vs 0.522, significant);
  - against the AI members played until now (v3, one step ahead), the new AI scores **66.3%** (62.5–70.0%).

  The arenas chose the network alone over blending in the hand-written heuristic. v3 stays for rollback
  (`CLAIRVOYANCE_VERSION=clairvoyance-v3`).
- **`intent-v3`:** 45 intents, up from 30.
  - New intents: `/guide`, `/collect`, `/income`, `/send`, `/rob`, `/sell`, `/use`, `/bank history`, and every casino
    game by name.
  - New frozen test set: 95.6% (v2: 22.2%).
  - All 283 held-out phrases: 87.6% (v2: 65.4%).
  - The two older sets are 1 and 2 phrases behind v2.
- **`/ask` reads the details:** a command that needs arguments is shown pre-filled from the message ("flip a coin for
  100 on heads" → `/coinflip bet:100 choice:heads`). It is only shown as text, never run.
- **`/clairvoyance status`** shows the search depth and the intent model version.
- **Recalibration** now rescales the blend weight automatically, so it never changes a move choice.

### Docs
- `docs/ECONOMY.md`: games of chance (every return, the walking-away rules), loans, crypto.
- `docs/ML.md`: the turn lookahead, intent-v3, v4.
- The guidebook (in the bot and the review doc): the new game, loan, crypto, `/ask` and coaching rules.

### Tests
- **New:** `tests/test_exploits.py`, `tests/test_lookahead.py`, `tests/test_intent_args.py`.
- **Updated:** the slots, high-low and crypto tests for the new rules.

## Help visibility and role incomes (November 2026)

### Livelier earning, and robbery notices
- `/work` answers with one of 55 random lines: the server's own 40, plus 15 more in the same spirit.
- `/crime` picks from 50 lines when it pays off and 50 when you're caught.
- `/rob` now pings the member it targeted, in a short line above the embed (embeds never ping), when the robbery
  works and when the robber gets caught. Only that member is pinged. Nobody is pinged when their wallet was too empty
  to rob.

### The Rudhabot Guidebook: `/guide`
- **What it is:** the guidebook (14 chapters in two parts) is now in the bot, so the reviewed doc and the bot say the
  same thing. Members read Part 1 (getting started, earning, the bank, markets, shop, games, Cloudfall, lottery and
  birthdays, staying safe). Staff also see the Part 2 chapters for their rank: who sees what, bankers, moderators,
  security, admins.
- **Reading it:**
  - `/guide [chapter]` opens the contents or jumps to a chapter. It autocompletes to your own chapters.
  - Previous / Next read straight through.
  - The menus never expire, and someone else's click gets their own private copy.
- **Private by default:** `public:True` posts a member chapter in the channel. Staff chapters always stay private.
- **`/ask`** can now answer from the guidebook ("why can't I send money"), never quoting a staff chapter to a member.
- **The text** lives in `rudhabot/assets/guidebook.md`, written for Discord (no tables). The bot reads it at
  start-up.

### Members see only what they can use
- **`/help`** shows each person only the commands they can use: the Banker, Admin, Moderation and Security entries
  appear only to people with that role or clearance (the lowest rank that can use a command at all).
  - Sections with nothing to show disappear from the overview, the menu, the full list and the counts.
  - Clicking someone else's help menu shows your own commands.
  - The section option offers only your sections.
- **Private replies:** staff get `/help` as a private reply, so staff sections never show to members in a channel;
  members keep the public reply.
- **`/ask`** never cites or suggests a command the asker can't use.
- **Discord's command list** now hides staff commands from members:
  - Manage Messages for Helper commands;
  - Kick Members for `/kick`;
  - Ban Members for bans;
  - Administrator for admin commands;
  - Manage Server for `/banker`, `/audit`, `/review`.

  The banker role, and any `/security staff` role without the matching permission, needs an override in Server
  Settings → Integrations → Rudhabot; `/setup` says so. Every command still checks permissions itself.

### Role incomes
- **`/collect`:** collect the income your roles earn, all ready ones at once, to your wallet. Each role has its own
  cooldown (24 h unless set otherwise), and missed periods don't pile up.
- **`/income`:** every role income, biggest first, with yours marked and when they're ready.
- **`/set-income <role> <amount> [hours]`** (Admin only, since incomes create money):
  - sets or changes an income; `amount:0` removes it;
  - limits: up to 1,000,000 per collection, 1-720 h, at most 25 roles; not @everyone or bot roles;
  - every change goes to the audit channel.
- **Background:** incomes of deleted roles are dropped automatically. Payouts are audited as `/collect` and show up
  in the weekly report.

## One server, one system (November 2026)

Rudhabot now works as one integrated system for its home server. Most of this runs in the background; the visible
changes for staff are two commands (`/setup`, `/clairvoyance report`), a richer `/modlog`, and two buttons on security
alerts. No new model versions were trained: Clairvoyance learns this server's bar from staff verdicts by moving
bounded thresholds, never weights.

### Clairvoyance everywhere
- **Branding.** Every output a Clairvoyance model contributed to carries a "Clairvoyance · ..." footer with its logo:
  - the Cloudfall win bar, coach hints, Deck Doctor and similar cards;
  - finance tips in `/balance` and `/bank card info`;
  - `/ask` suggestions, now labelled "understood locally, N% sure";
  - review signals and the `/review` list;
  - security alerts with model evidence, the ranked digest, drills run with models;
  - the weekly report.

  Rule-only outputs aren't branded, so the logo always means a model was involved.
- **One status view.** `/clairvoyance status` (and `/security tools action:models`) shows every component, each with
  its health, its fallback, and what it has learned here:
  - Cloudfall;
  - the intent router;
  - the anomaly monitor;
  - the security models;
  - the language model.

  The Cloudfall details follow in a second embed.
- **Learning from this server (no new weights):**
  - **Security verdicts:**
    - every alert has **Real** and **False alarm** buttons (Helper+). Verdicts are kept in SQLite, and a later press
      replaces an earlier one;
    - after 5 verdicts on model-backed alerts, the confidence the message and incident models need moves between
      0.80 and 0.95 with the false-alarm share;
    - detectors that are mostly false alarms here get a health-check hint naming the setting to adjust.
  - **Review verdicts:** `/review resolve` outcomes move the anomaly monitor's bar between 0.58 and 0.74 (base 0.62),
    remembered across restarts.
  - **Intent outcomes:** the router counts suggestions offered, run and clarified, per intent. It stores counts only,
    never what people typed.

### Member standing: one view of a member across every system
- **The score.** A standing score and band (new, regular, established, trusted; or restricted while on probation or
  in quarantine) built from this server's own records:
  - tenure;
  - active days (daily message counts, never content);
  - moderation cases (automatic ones count half, and cases fade over 6 months);
  - security incidents (false alarms don't count);
  - confirmed economy anomaly signals.

  Trusted needs 30+ days here and 14+ active days. No identity attributes are used.
- **Trusted members are spared automatic restrictions.** Automatic timeouts and probation skip them, and staff get a
  proposal instead (in shadow mode this is only noted). Their scam messages are still removed, because established
  accounts do get compromised. New joiners are never trusted, so during a raid the check costs nothing.
- **`/modlog` is the member file:** standing and why, cases, incidents (with staff verdicts), anomaly signals and any
  economy hold.
- **Cross-references:**
  - a security alert says when a member in it is established ("possibly a compromised account") or has economy
    signals;
  - a review signal shows the member's standing and security record.
- **Economy holds:**
  - accounts on probation or in quarantine can't send or receive money, rob or be robbed, or take loans;
  - during a raid alert (threat level 3+), accounts that joined in the last 24 hours can't transfer;
  - members are told why their own account is held, but never why someone else's is.

### One home server and one place for its set-up
- **`RUDHABOT_GUILD_ID`** names the home server: commands are registered there (they appear instantly, with no global
  copies), and commands from other servers are politely refused. Unset, the home is the server that holds the
  configured channels, and nothing is refused.
- **`/setup` (Admin)** collects everything in one view, next to the security set-up:
  - channels: notifications, lottery, birthdays, audit, reviews, reports;
  - roles: admin, banker, bankrupt.

  Changes apply live, and `reset:` puts a setting back to its default. Every part of the bot reads these values when
  it needs them: announcements, audit and review posts, loan notices, the bankrupt role, admin and banker checks, and
  security's admin tier.
- **The admin role is guarded.** Changing it needs the Administrator permission; @everyone and bot roles are
  refused; admin and banker role changes are announced in the security alert channel.

### State of the Realm
- **Daily economy snapshots** (kept 400 days):
  - money supply;
  - accounts holding money;
  - credit and loans owed;
  - the day's movements and active accounts;
  - where money came from and went (net change per command or job).
- **The weekly report.** A Clairvoyance-branded State of the Realm is posted every Sunday from 20:00 Rudhanese Time
  to the new reports channel, which follows the review channel; `/setup` can move it. Admins can see it at any time
  with `/clairvoyance report`. It covers:
  - the economy trend, and money sources and sinks;
  - security incidents, actions taken, and what shadow mode would have done;
  - staff verdicts;
  - community activity;
  - Cloudfall results;
  - Clairvoyance's health and calibration;
  - birthdays this week.

### Security
- **Slow-drip raid detection.** Three or more members who joined in the last day (`messages.drip_accounts`) posting
  the same unofficial site, or the same message around a rotating link, within 24 hours raise one phishing signal
  that covers every post in the wave.
  - Official and everyday sites never count, nor do longer-standing members.
  - The wave is acted on only when the links carry another warning sign (a risky domain, a lure) or the wave is two
    accounts past the bar; otherwise it is an alert.
  - Raising the threshold is announced as weakening the defences.
- **Response:**
  - each post is handed over for removal once;
  - a risky wave's accounts get probation (the wave, and only the wave);
  - the drip's own score never raises the server's threat level, so other new members aren't restricted for hours.
- **Wargame** (the same 6 test servers × 40 days and seeds as the published benchmark):
  - **slow-drip posts removed:** 54% → **100%**;
  - **its accounts on probation:** 25% → **100%** (44% → 100% with models);
  - **innocent messages removed:** still 0;
  - **innocent probations:** 1.03 → 1.11 per day (from standing orders while the level is raised);
  - every other attack family is unchanged.

  The simulator's quiet-day false-alert figure moved from 0.018 to 0.028. Counted directly, quiet-day alerts are
  identical with and without the rule; the difference is an incident that stays open past midnight being counted
  again the next day.

### SQLite
- New tables:
  - `member_activity` (daily counts);
  - an incident-to-member index (backfilled once from stored incidents);
  - `sec_verdicts`;
  - the server profile;
  - `economy_daily`.

  All follow the retention sweep where they hold member data.
- **Ledger time index.** `Ledger.since()` on both backends lets the anomaly refit and the snapshots read only the
  window they need.
- **`AuditService.recent()`** (`/bank history`, `/audit`, finance tips) answers from the SQL index instead of loading
  the whole ledger into memory.

### Fixes and small changes
- `/help`:
  - documents `/setup` and `/clairvoyance report`;
  - describes `/modlog` as the member file;
  - now says `/add-money` and `/remove-money` are Admin only, which is what the code always enforced;
  - describes `/clairvoyance status` as the full view.
- `.env.example`: documents `RUDHABOT_GUILD_ID` and `RUDHABOT_REPORTS_CHANNEL_ID`, and notes that `/setup`
  overrides channel and role ids live.
- New local data files for the JSON backend are gitignored: `intent_outcomes.json`, `server_profile.json`,
  `economy_daily.json`.

### Tests
- **Added:**
  - `tests/test_clairvoyance_integration.py`: branding, calibration bounds, verdict buttons, the status view, intent
    outcome counts;
  - `tests/test_standing.py`: scoring, trusted protection, economy holds, cross-references, the member file, the
    incident index backfill;
  - `tests/test_profile.py`: profile overrides, live reads, the admin-role guard, home-server refusal;
  - `tests/test_realm.py`: ledger windows, the SQL-backed audit trail, snapshots, the weekly report and its posting.
- **Extended:** the slow-drip tests in `tests/test_security_core.py`, including the hard negatives.

Nothing here has been run against the live server yet. Security stays in shadow mode until an admin switches it live.

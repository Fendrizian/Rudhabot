# Changelog

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

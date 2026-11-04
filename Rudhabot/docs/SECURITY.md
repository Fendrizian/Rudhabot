# Server security

Rudhabot defends the server against raids, spam, phishing, compromised staff accounts, rogue bots and webhooks. It
also keeps the moderation log and helps staff recover. It complements Discord's own safety settings and human
moderators; it doesn't replace them, and no bot can guarantee a server's security.

How it works, in one line: **deterministic rules detect and plan; Clairvoyance's models only add evidence and rank
alerts; staff decide anything consequential; every action is re-authorised when it runs and recorded with its
outcome.**

## Quick start

1. **Discord Developer Portal → Bot:** turn on **Server Members Intent** and **Message Content Intent**. The bot also
   uses the (non-privileged) moderation intent for audit-log events.
2. **Role position:** Rudhabot's role must stay **above every staff role** (as it is now), or it can't quarantine a
   compromised staff account. Discord never lets a bot act on the server owner.
3. Create a staff-only **alert channel**, a **mod-log channel** and a **probation role**. The probation role should
   not be able to post links or attach files, or should see only a welcome channel.
4. `/security setup alert_channel:#security-alerts modlog_channel:#mod-log probation_role:@Probation`.
5. Map staff roles to clearance tiers: `/security clearance set role:@Moderator tier:2`, and so on. (Discord
   permissions also grant tiers by default, see below.)
6. `/security posture`: fix what it reports (2FA for moderation, verification level, dangerous roles, ...).
7. `/security automod action:create` adds Discord-native AutoMod rules (mention spam, spam, denied links).
8. Leave it in **shadow mode** (the default) for a while. Everything is detected and reported, and the automatic
   actions are recorded as "would have". When the reports look right, switch on automation with `/security mode live`.
9. Rehearse: `/security drill scenario:join_raid` (and the other scenarios). Drills run in a sandbox.

## Clearance tiers

| tier | name | military rank (flavour only) | can |
|---|---|---|---|
| 0 | Member | civilian | report messages |
| 1 | Helper | corporal | warn, timeouts up to 10 min, slowmode, purge up to 50, release probation |
| 2 | Moderator | sergeant | timeouts up to 28 days, kicks, purge up to 500, request lockdowns, confirm proposals |
| 3 | Senior Moderator | lieutenant | bans, mass bans, unbans, approve lockdowns, set the threat level, exceptions, snapshots |
| 4 | Admin | commander | configuration, shadow/live mode, restores, releasing quarantine, clearance, AutoMod |
| 5 | Owner | head of state | everything |

A member's tier is the highest of these:
- their roles' mapped tiers (`/security clearance set`);
- the configured admin role (tier 4);
- if `derive_tiers` is on (the default), their Discord permissions: Administrator or Manage Server gives Admin, Ban
  Members gives Senior Moderator, Kick or Timeout Members gives Moderator, Manage Messages gives Helper.

Nobody can act on themselves or on anyone of equal or higher tier. A quarantined account has tier 0 until it is
released. The tier is checked **when the action runs**, including when an approval button is pressed, not only when
a command or button is shown.

## Threat levels and standing orders

| level | flavour | standing orders (carried out in live mode, reported in shadow mode) |
|---|---|---|
| 1 Normal | garrison routine | obvious spam removed; repeat spammers timed out briefly |
| 2 Elevated | sentries doubled | + 5 s slowmode on channels under a surge; new accounts go on probation when they join |
| 3 High | checkpoints up | + 15 s slowmode; every join on probation; links from members who joined today removed; staff asked to confirm pausing invites; anti-nuke budgets ×0.75 |
| 4 Severe | martial law | + 30 s slowmode; staff asked to approve a lockdown of the affected channels; budgets ×0.5 |
| 5 Critical | fortress | + 60 s slowmode; staff asked to approve a server-wide lockdown and pausing DMs; budgets ×0.25 |

The level is raised automatically by incidents, up to `levels.auto_max` (default 4: Critical is staff-only). One
spammer doesn't raise it: member-driven incidents count fully only once several accounts are involved. After
`levels.decay_minutes` (30) quiet minutes the level steps down by one. A level set by staff
(`/security level`) holds until its hold ends. The level survives restarts.

## What is detected

All of these are deterministic rules with reason codes, so an alert says *what was observed*, never just a score.

- **Joins:**
  - **Surges:** the join rate compared with this server's own learned baseline for that hour of the week (Poisson
    test, 1- and 5-minute windows).
  - **Cohorts:** accounts that joined within minutes and share traits: created within two days of each other, no
    profile picture, the same name pattern, near-identical names, an identical avatar, the same invite.
  - **Discord's spammer flag.**
  - **Possible ban evasion:** a profile matching a banned account's avatar hash or name stem. This only ever
    produces an alert. Account age alone is context, never evidence.
- **Messages:**
  - **Floods** from one member.
  - **Duplicates** that survive spacing, case and invisible-character tricks.
  - **Near-duplicates across accounts:** MinHash over character 5-grams, with LSH. Only signatures are kept, for
    3 minutes. They count as spam only with a payload (links, invites, mass pings) or mostly brand-new accounts, so
    fifty "happy birthday!" messages are not spam.
  - **Mention bombs** and mention floods, and **@everyone attempts**.
  - **Invites** to other servers (flag or delete; this server's own invites are allowed).
  - **Channel surges**, measured against each channel's learned rate.
  - Relaxed channels (`channels.relaxed`, e.g. roleplay battles) get 3× the limits.
- **Links (no fetching):**
  - look-alikes of protected brands (`dlscord`, `steamcomunnity`);
  - homoglyph and punycode domains;
  - a brand name on someone else's domain or sub-domain;
  - the `user@host` trick and masked links whose text shows a different site;
  - raw IP hosts, URL shorteners, known IP loggers;
  - this server's deny and allow lists.
- **Attachments, by metadata only (nothing is downloaded):** programs and scripts, disguised double extensions
  (`invoice.pdf.exe`), right-to-left filename tricks, a declared type that doesn't match the name.
- **Webhooks:** a webhook flooding a channel.
- **Anti-nuke, from the audit log:**
  - **Budgets:** every high-impact change has points (deleting a channel 3, a ban 1, pruning 8, ...), and each
    account may spend a budget per 10 minutes: Moderator and below 8, Senior 12, Admin 20, scaled down at higher
    threat levels. Going over the budget triggers containment.
  - **Escalations:** granting dangerous permissions to @everyone or a broad role. Granting @everyone
    Administrator-class permissions trips any budget at once.
  - **Other flagged changes:** giving members roles with dangerous permissions; dangerous channel overwrites for
    @everyone; weakening the verification level, 2FA requirement or explicit-media filter; removing or disabling
    AutoMod rules; adding bots; creating webhooks.
  - Rudhabot's own actions never count, and allow-listed accounts are exempt.
  - Clean up raids with `/massban`: it doesn't count against anyone's budget. Manual bans in Discord do count.
- **Member reports:** the *Report to moderators* message command.

## What is done, and who decides

| response | authority |
|---|---|
| delete the flagged messages | automatic (live mode) |
| slowmode on a surging channel (lifted automatically after 15 min) | automatic |
| probation for risky joins (lifted automatically after `probation_minutes`) | automatic |
| timeouts of at most 10 minutes for repeat spammers or mention bombs from new members | automatic |
| **anti-nuke quarantine** of an account over its budget: its roles with dangerous permissions are removed, plus a 10-minute timeout. A bot over budget is kicked | automatic (the one exception for staff) |
| containment in the same window: remove webhooks and bots that account added; undo dangerous grants to @everyone | automatic |
| longer timeouts, kicks, bans (including banning a cohort) | proposed: one staff member of the required tier confirms |
| pausing invites / DMs (Discord's own incident actions, which expire by themselves) | proposed: Senior Moderator confirms |
| lockdowns, restores from a snapshot | proposed: **two different staff** approve (`approvals.two_person`) |

Automatic actions never restrict staff, except anti-nuke quarantine. Models never trigger an action on their own (see
below). Proposals arrive in the alert channel with **Approve** and **Decline** buttons. Each request:
- has a one-use token and expires after 20 minutes;
- is answered by compare-and-set, so two people clicking at once can't both carry it out;
- re-checks the presser's clearance at the moment of pressing.

Shadow mode governs only *automatic* actions. A proposal a human approves is a human decision and is carried out in
either mode.

Every action is recorded as *attempted* and then *confirmed* (Discord accepted it), *partial*, *failed* (with the
reason), *refused* (not authorised), or *shadow* (would have run). Nothing is retried blindly. Discord calls are paced
(a few per second, one bulk job at a time). Bulk bans use Discord's bulk-ban endpoint, 200 accounts per request.

## Lockdowns

`/security lockdown start scope:server|channel|category`:
1. **Preview:** exactly which overwrites change.
2. **Approval** by a second staff member.
3. **Snapshot**, then the change.
4. **Expiry:** it lifts itself, by default after 30 min (at most 12 h).

What changes: in each channel in scope, @everyone loses Send Messages, thread posting and creation, reactions and
polls. Any non-staff role that explicitly allows speaking there is neutralised too. These are left alone: staff
roles, Rudhabot's own role, protected channels (`channels.protected`) and the alert and mod-log channels.

Protections:
- Overlapping lockdowns are refused.
- Restart-safe: the plan is stored before anything changes, and expiry timers survive restarts.
- Restoring puts back each overwrite **only if it is still exactly what the lockdown set**. Anything a moderator
  changed meanwhile is left as they made it and listed under **Fix manually**, along with:
  - channels the bot couldn't edit;
  - member-specific overwrites that still allow speaking;
  - partial failures.

**If Rudhabot is offline during a lockdown:** for every channel listed in the lockdown report, open
*Edit Channel → Permissions*, select @everyone, and set *Send Messages*, *Send Messages in Threads*, *Create Threads*,
*Add Reactions* and *Create Polls* back to their previous state (usually the neutral "/"). Also re-allow Send Messages
for any role the report lists. `/security status` shows active lockdowns; the alert-channel post lists the channels.

## Snapshots and restore

Snapshots record roles (permissions, colour, hoist, mentionable, position), channels and categories (type, topic,
slowmode, NSFW, every permission overwrite) and the safety settings. They are taken every 6 hours, before every
lockdown, and on demand (`/security snapshot take`); the last 30 are kept.

`/security snapshot restore` previews a diff and needs approval. A restore:
- recreates deleted roles and categories/channels, with overwrites re-pointed at recreated roles;
- puts back changed role permissions, overwrites and weakened safety settings;
- never deletes anything added since;
- never edits roles above Rudhabot's role.

What can't be restored: message history, pins, webhooks and invites in deleted channels, and who held a deleted role.
Discord doesn't let bots see that.

## Incidents, the moderation log and records

**Incidents.** Signals of one kind that occur close in time are grouped into an **incident** (raid, spam, phishing,
anti-nuke, webhook/bot, ban evasion). Incidents at severity 50% or more, and every anti-nuke incident, are posted at
once, with:
- the observations (reason codes);
- the scope;
- the actions with their statuses;
- up to two spoilered evidence snippets.

Weaker ones are listed in a 10-minute digest, ordered by the alert ranker (most likely real first). An incident
records:
- start and end times;
- its signals (structured, without message text);
- every action attempted, succeeded or failed;
- staff decisions;
- the rule, config and model versions.

`/security incident report` writes an after-action report (an LLM draft if configured). `/security incident redact`
deletes its evidence snippets. Incidents close after 30 quiet minutes.

**Cases.** Every moderation action, manual or automatic, gets a numbered **case** in the mod-log channel. `/case edit`
corrects a reason (the old reason is kept in the record's edit history), `/case delete` removes a case (Admin), and
`/modlog` shows a member's history.

## Privacy and retention

- Message text is analysed in memory and **not stored**. Duplicates and coordination use hashes and MinHash
  signatures, kept in memory for minutes.
- For **flagged** messages only, a snippet of at most **200 characters** is kept as evidence for **14 days**
  (`retention.snippet_days`).
- Closed incidents (with their actions) are deleted after **90 days** (`retention.incident_days`).
- Ban-evasion fingerprints (avatar hash, name stem of banned accounts) are kept for a year.
- Staff "last seen" timestamps (for stale-staff checks) are kept; nothing about ordinary members.
- No demographic or identity attributes are used anywhere. No moderator decision is turned into training data.
- Records are per server, readable by Helpers and above, and kept in `data/rudhabot.db` (backed up daily).

## Clairvoyance's role

All of these are advisory, can each be switched off (`/security config set models.message off`, or
`RUDHABOT_SECURITY_MODELS=0` for all), are refused if their feature schema doesn't match the code, and run off the
event loop with a 2-second timeout. If one fails, the rules carry on alone.

- **Message-risk model** (8.4 million parameters, a hashed-embedding network with scam, spam and malicious-URL heads).
  It scores messages with links, files or invites, messages from new members, and anything a rule already flagged.
  - At ≥ 0.8 it adds a model signal, which can raise an incident's severity by at most 0.15.
  - Between 0.4 and 0.8 a reading is *uncertain* and adds nothing.
  - It **never deletes or punishes on its own**. When `auto.model_corroborates` is on, a reading of ≥ 0.97 can be the
    *second* piece of evidence for deleting a message in which a rule already found a risky link.
- **Incident model:** every minute, it reads multi-scale activity counts and gives the probability of an attack and
  its likely kind. A confident reading adds evidence to an open incident; on its own it only reaches the digest.
- **Alert ranker:** orders the low-severity digest by how likely each flag is a real attack. On the benchmark it
  beats severity there, but not among full alerts, so alerts aren't re-ordered by it.
- **Staff baseline:** each staff account's own usual rate of high-impact actions. A wildly unusual burst is flagged
  even under budget, but only budgets trigger quarantine.

How they were trained and how well they work, with confidence intervals and what the numbers do and don't show:
`docs/ML.md` and `models/security/BENCHMARK.md`.

## The LLM

When OpenAI is configured (`docs/OPENAI.md`), it does two things:
- drafts incident summaries (`/security incident report`);
- answers staff questions read-only (`/security ask`).

It receives only content-free facts: reason codes, counts, times, action statuses. It gets no message text, no
snippets and no member data. Its output is labelled as a draft, has mentions neutralised, and is never used to take
an action. It has no tools. If it is unavailable or rate-limited, the deterministic report is used.

## Temporary role tools

For when staff can't manage roles in Discord themselves (Rudhabot's role sits above theirs). The commands:
- `/role add` and `/role remove` give or take a role;
- `/role move` places a role at a position, or just above or below another role;
- `/role perms` turns one permission on or off for a role, or shows its permissions. It can also edit @everyone
  and other bots' roles (never Rudhabot's own), but never gives @everyone a dangerous permission;
- `/role list` shows the hierarchy with what Rudhabot can and can't manage, and whether Rudhabot itself still has
  Manage Roles. Use it first when roles "stop working".

The rules:
- **Who:** Admin clearance, checked on every use. A quarantined account has none.
- **Which roles:** only roles below Rudhabot's highest role; never @everyone or roles managed by integrations
  (bots, boosts).
- **Taking roles away:** nobody can take roles from someone of equal or higher rank, except the owner.
- **Records:** every change is a mod-log case, and counts against the *requester's* anti-nuke budget (Rudhabot's
  own actions are otherwise exempt), so the tools can't be used to hand out power unnoticed.
- **Alerts:** granting a role with dangerous permissions, moving one up, or turning on a dangerous permission also
  posts an alert. Turning on Administrator-class permissions for a broad role (25+ members) trips the requester's
  anti-nuke budget at once.
- **Switching off:** set `RUDHABOT_ROLE_TOOLS=0` and restart once the hierarchy is fixed.

If Rudhabot itself has lost Manage Roles or its role was moved down, only the server owner can fix that, in
*Server Settings → Roles*.

## Permissions Rudhabot needs

Rudhabot has Administrator on this server. That works, but is broader than security needs (the posture check says
so). The minimum, and what stops without each:

| permission | for |
|---|---|
| View Audit Log | anti-nuke (no audit events without it) |
| Manage Roles | probation, quarantine, verification, restoring roles |
| Manage Channels | slowmode, lockdowns, restoring channels |
| Manage Messages | deleting spam, purge |
| Moderate Members | timeouts |
| Kick Members / Ban Members | kicks, bans, removing rogue bots |
| Manage Webhooks | removing rogue webhooks |
| Manage Server | AutoMod rules, pausing invites and DMs, reading invites |
| View Channel, Send Messages, Embed Links, Read Message History | alerts and the mod log |

## Configuration reference

`/security config view` lists every setting with its current value; `/security config view key:<name>` explains one.
Changes are versioned (`/security config history`, `/security config rollback`). The main groups:
- `joins.*`, `messages.*`, `links.*`, `files.*`: detection thresholds.
- `antinuke.*`: budgets, window, quarantine and containment switches.
- `levels.*`: the highest automatic level and the decay time.
- `auto.*`: which automatic actions live mode may take, the timeout length, model corroboration.
- `approvals.*`, `lockdown.*`, `retention.*`, `models.*`, `llm.summaries`.

Lists: `links.deny`, `links.allow`, `channels.relaxed`, `channels.protected`, `channels.ignored`
(`/security config list`). Time-limited exceptions (at most 90 days) for a user, role, channel or domain:
`/security allow`.

## If a token leaks

1. Discord Developer Portal → your application → Bot → **Reset Token**; put the new one in `.env`; restart.
2. OpenAI: revoke the key in the OpenAI dashboard and create a new one.
3. Check the audit log for actions by Rudhabot that it didn't record (`/security incidents`, the mod log).
4. Logs never contain tokens: a filter replaces Discord tokens, OpenAI keys and webhook URLs before anything is written.

## Tests

`tests/test_security_core.py`, `tests/test_security_service.py` and `tests/test_security_cogs.py` run against an
in-memory fake server: nothing touches Discord.

They cover:
- every detector, including the hard negatives: birthday floods, roleplay battles, onboarding waves, staff floods;
- anti-nuke containment, and the owner and Rudhabot never being targeted;
- shadow vs live mode;
- the clearance rules, approvals (two-person, replay, expiry, decline) and lockdowns (preview, overlap, restart,
  expiry, a moderator's change during a lockdown, partial failure);
- snapshot restore, probation and slowmode expiry, retention;
- models unable to bypass authorisation, LLM data minimisation and log scrubbing.

This has **not** been tested on a live server: run the drills and stay in shadow mode first.

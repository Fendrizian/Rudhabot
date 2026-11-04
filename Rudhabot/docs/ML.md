# Clairvoyance: Rudhabot's in-house machine learning

Clairvoyance (formerly "the Oracle") is the ML that Rudhabot trains and serves itself. It uses no hosted model, no
pretrained weights and no user data:
- the Cloudfall model learns only from engine self-play;
- the intent router only from developer-written phrases;
- the anomaly monitor fits the server's own audit ledger at run time;
- the security models learn from public licence-checked datasets and a wargame simulator (section 5).

| Task | Model (served) | Trained on | Used for |
|---|---|---|---|
| **Cloudfall value network** | `clairvoyance-v3`: slot-encoder network, 175k params | engine self-play only | AI opponent, coaching hints, the live win bar, turning points, Deck Doctor, similar cards |
| **Intent router** | `intent-v2`: hashed n-gram MLP, 30 intents | developer-written phrases | `/ask` and @mentions: map plain words to an existing command, or ask a clarifying question |
| **Anomaly monitor** | isolation forest + personal baselines + rules | the server's last 7 days of hourly activity windows | review signals for moderators (`/review`) |
| **Message-risk model** | `message-risk-v1`: hashed-embedding multi-task network, 8.4M params | public licence-checked datasets + synthetic server chat | security evidence for scam, spam and malicious-URL messages |
| **Incident model** | `incident-v1`: MLP over multi-scale activity counts, 7.8k params | wargame simulator days | security evidence: "an attack of kind X is under way" |
| **Alert ranker** | `ranker-v1`: logistic model over incident evidence | wargame simulator incidents | ordering the low-severity security digest |
| **Staff baseline** | per-account Poisson baselines, learned online | the server's own audit log | flags staff acting far outside their own pattern |

It also produces private **finance insights** in your own `/balance` and in `/bank card info`, derived from your own
ledger.

**Trust boundary (all tasks):** Clairvoyance only advises.
- Game moves are applied by `engine.apply`, which re-validates them.
- Commands run through their own permission checks.
- Review signals never change balances or permissions.
- Insights never change limits, interest or charges.

A consolidated, reviewer-friendly summary of every training run (oracle-v1, v2, v3, intent v1/v2, anomaly v1/v2) is
produced as `clairvoyance-training-results.zip` (see its README).

## 1. Cloudfall value network

### What it predicts
P(win | state, perspective), from what that player can legitimately see. `encode()`/`encode_v2()` never read the
opponent's hand or either deck's order, and the tests prove it by scrambling hidden zones. Before searching, the AI
re-samples hidden information (determinization), so it gets no information a human in its seat wouldn't have.

### Architectures
- **v1 `ClairvoyanceNet`** (`oracle-v1`, kept for rollback): 235 dense features plus three card-count vectors pooled
  through a 16-d learned card embedding; an MLP of 128 → 64 → 1. 46,465 parameters.
- **v2 `ClairvoyanceSlotNet`** (`clairvoyance-v2`, `clairvoyance-v3`): each of the 8 field slots is encoded by one
  shared encoder (the slot's 20 attributes, the card embedding of the unit there, and its attachments pooled through the
  same embedding; 68 → 48, ReLU). The slot encodings stay positional (adjacency matters for Taint spread and
  Thunderclap) and join the global features and pooled hand/board embeddings in a 256 → 128 → 1 MLP. 175,217 parameters,
  pure numpy with hand-written backprop (numerically gradient-checked).

### How v3 was trained (same architecture and features as v2)
`python -m rudhabot.ml.train --version clairvoyance-v3 --init clairvoyance-v2 --games 8000 --test-games 600 --gens 3
--epochs 10 --lr 1e-3 --ema 0.999 --hide-hand 0.15 --eval-games 300 --blend-weights 0,0.25,0.5,0.75,1`, then
`python -m rudhabot.ml.recalibrate --version clairvoyance-v3`.

1. **Warm start + expert iteration:** 3 generations × 8,000 games starting from v2, played by the *served* policy (the
   blend), mixed with heuristic and random seats; 40% of decks are legal random mutations of the archetypes.
2. **TD(λ) targets** (λ = 0.7) blending the final result with the acting network's afterstate estimates; evaluation
   always uses true results.
3. **Hand-hidden augmentation:** 15% of every training batch sees no own hand. The spectator win bar always hides
   hands, and earlier models never trained on that input.
4. **EMA (Polyak) weight averaging**, decay 0.999; early stopping keeps the best validation epoch. Networks
   still peak early: v3's best epochs per generation were 3, 1 and 1 (v2's: 1, 2 and 3).
5. **Serving blend weight chosen by arenas** among 0 / 0.25 / 0.5 / 0.75 / 1, stored as `serving.blend_weight`.
6. **Calibration:** Platt scaling fitted on a broad mixture (heuristic-played and AI-played games, a third with the
   hand hidden; seeds 8.1M+). The blend weight was rescaled with it, so the AI's move choices are exactly the measured
   ones.
7. **Seeds are disjoint:** train 0…, calibration 8,000,000… and 8,100,000…, test 9,000,000…, training arenas
   5,500,000…, benchmark 7,700,000… / 7,800,000… / 7,900,000….

Build: 23 minutes on 4 cores (v2 took 66 minutes for fewer games; see Efficiency).

### Measured results (fresh-seed benchmark: `python -m rudhabot.ml.benchmark`)
All three models on *the same* positions from 500 heuristic-played and 500 AI-played games (true outcomes);
paired 95% intervals come from a bootstrap that resamples whole games. Lower log-loss / Brier / ECE is better.

| | oracle-v1 | clairvoyance-v2 | **clairvoyance-v3** |
|---|---|---|---|
| Log-loss, heuristic-played positions | 0.476 | 0.480 | **0.470** |
| Log-loss, AI-played positions | 0.468 | 0.447 | **0.439** |
| AUC, AI-played positions | 0.858 | 0.871 | **0.875** |
| Calibration error (ECE), heuristic / AI-played | 0.033 / 0.038 | 0.011 / 0.025 | **0.008 / 0.015** |
| Spectator win bar log-loss (hand hidden) | 0.493 | 0.493 | **0.481** |
| Served AI vs heuristic (600 games) | 56.7% (52.7–60.6) | 60.3% (56.4–64.2) | **63.7% (59.7–67.4)** |
| … with mutated (unfamiliar) decks | 57.2% | 63.7% | **65.8%** |
| vs random play | 97.1% | 96.7% | **98.2%** |
| One evaluation / one AI move (p50, one thread) | 0.019 / 0.38 ms | 0.061 / 0.61 ms | 0.057 / 0.60 ms |

Head to head: v3 beats v2 53.1% and v1 54.0% (600 games each); v2 beats v1 54.2%.

- **Established at 95%:** v3 is better than v2 on heuristic-played positions (Δ log-loss −0.011, Brier and AUC too),
  on the spectator win bar (Δ log-loss −0.012), and in calibration; it beats the heuristic by more than v2.
- **Not established:** v3's edge over v2 on the AI's own positions (Δ log-loss −0.008, interval just touches 0) and
  head to head (53.1%, interval includes 50%).
- Early-game positions (rounds 1–3) are hard for every model (AUC ≈ 0.67–0.72): the game is genuinely open then.

Training-time numbers in the manifest (training arena seeds) differ a little from these fresh-seed numbers, e.g. v2
measured 66.5% vs the heuristic on its own arena seeds and 60.3% here. The benchmark numbers are the honest ones.

### Efficiency
- **Subnormal weights:** weight decay had driven 27,827 of v2's weights below 1.2e-38, which CPUs multiply ~100×
  slower. `load_net` and training now set them to exactly 0 (predictions change by less than float32 precision):
  one batched forward pass 0.80 → 0.11 ms (7.4×).
- **One BLAS thread per process:** OpenBLAS's default thread pool, used by every worker process on tiny matrices, made a
  100-game arena 20.9 s instead of 2.4 s (8.6×). `rudhabot.ml` pins BLAS to one thread before numpy loads; main.py does
  the same for the bot (inference runs in worker threads next to the event loop).
- **Engine and encoder:** one pass computes a unit's power and stamina (`engine.base_stats`) instead of three; lookup
  tables are no longer rebuilt per call; cloning a game no longer seeds a random generator from the OS. Encodings are
  bit-identical (checked on 400 positions).
- **Win bar:** both players' views are evaluated in one batched pass.

### Lifecycle and rollback
- **Artifacts:** `models/<version>.npz` (architecture header, weights, calibration, metadata) and `models/manifest.json`
  (architecture, feature-schema hash, config, per-epoch history, metrics, arenas, blend search, recalibration,
  latency and learned card values).
- **Safety on load:** a schema or architecture mismatch is refused and the bot falls back to the heuristic.
- **Rollback:** `CLAIRVOYANCE_VERSION=clairvoyance-v2` (or `oracle-v1`), or `/clairvoyance reload version:<name>`.
  `CLAIRVOYANCE_ENABLED=0` turns the model off.
- **Monitoring:** `/clairvoyance status` shows the version, health, metrics, arena results, calls, fallbacks and latency.

## 2. Intent router (`python -m rudhabot.ml.intent`)

- **Task:** map a message to one of 30 intents. Each intent is an existing capability (balance, inventory, shop,
  `/look`, loans, bank tiers, credit card, stocks, `/portfolio`, each game, Cloudfall play/rules/collection, wishes, …)
  or "chat".
  - "chat" goes to the OpenAI assistant, or to the local docs.
  - At or above the model's **confident threshold**, the intent is offered as a button that runs the **real** command,
    with its own checks, for the person who asked only. Commands that need details (amounts, bets) are explained.
  - Between 25% and that threshold, Rudhabot asks which of the top options was meant.
- **Model:** hashed character 2–5-grams plus word uni/bi-grams form an 8,192-d TF vector, then a ReLU layer and a
  softmax; temperature-calibrated on held-out seed phrases.
- **Data:** developer-written phrases (`ml/intent_data.py`), augmented with politeness, casing and typo variants.
- **Evaluation:** two held-out test sets that are never trained on: `TEST` (58 phrases, from v1) and `TEST_2` (135
  phrases, written and frozen *before* v2's training data was changed), plus grouped 5-fold cross-validation over the
  training phrases. The confident threshold is the lowest whose cross-validated precision reaches 90%.

| | intent-v1 | **intent-v2** |
|---|---|---|
| Intents | 28 | 30 |
| Training phrases (before augmentation) | ~280 | 520 |
| Accuracy, `TEST` (58 phrases) | 81.0% | **81.0%** |
| Accuracy, `TEST_2` | 84.8% (the 125 phrases it can route) | **87.4%** (135 phrases) |
| Grouped 5-fold cross-validated accuracy | n/a | 78.8% |
| Confident threshold / coverage / precision on `TEST_2` | 0.60 / 84.8% / 90.6% | 0.72 / 76.3% / **99.0%** |
| Small talk confidently offered as a command (`TEST_2`) | 25.0% | **12.5%** |
| A task sent to chat instead (`TEST_2`) | 0.9% | 3.9% |
| Calibration error (`TEST_2`) | n/a | 0.050 |

Every misrouted test phrase is listed in `models/intent-v2.json`. Fixes beyond accuracy: the `card` intent pointed at
`/card info`, which doesn't exist (it is `/bank card info`); `bank` still described the removed savings account (it now
opens `/bank tiers`); the trainer's first epoch saw the data sorted by intent.

## 3. Anomaly monitor (`ml/anomaly.py`, `services/review.py`)

- **Features:** per-user hourly windows from the audit ledger and the failed-interaction log: entries, money in and
  out, largest single change, net change, distinct sources, gambling wins, transfers received from other people,
  failed interactions.
- **Model:**
  - an isolation forest (numpy, 100 trees × 256 samples), refitted on the server's own last 7 days at startup and
    every 6 h, so "unusual" means unusual *for this server*;
  - **personal baselines:** a member with at least 5 hourly windows is judged relative to their own usual activity
    (their median window is moved onto the server's median), so a high-roller's normal big bets are ordinary while a
    sudden change in anyone's behaviour stands out;
  - an **extreme-deviation test** on the suspicious features (money in, largest change, net, gambling wins, transfers
    received, failures), both baseline-centred (z ≥ 7) and raw (z ≥ 8), which catches one extreme feature that the
    forest dilutes among nine;
  - robust z-scores use a spread floor (0.5 in log units), so a feature that is usually 0 no longer turns one failed
    click into "z 693".
- **Rules:** also during cold start (before 120 windows): 15+ failures in an hour, a single change of 1,000,000 or
  more, or 12+ transfers in from other people.
- **Judging:** each member at most once a minute; activity inside that minute is judged by a 15-second sweep, so a
  burst that ends within a minute is still looked at. One open signal per member per 6 h.
- **Output:** a signal is stored in `reviews.json` and posted to the review channel; a moderator dismisses or confirms
  it with `/review resolve`. **Nothing else happens.**
- **Synthetic benchmark** (`python -m rudhabot.ml.anomaly --tune`): servers of 150 members over 72 hours with 30 injected
  anomalies of four kinds (money mule, implausible win streak, failure storm, one huge movement). 5% of ordinary
  members are legitimate high-rollers who must not be flagged, and anomalies have random intensity, so some are subtle.
  Settings are tuned on seeds 0–9 and reported on untouched seeds 100–119:

  | | precision | recall | recall by kind (mule / streak / failures / huge) |
  |---|---|---|---|
  | v1 detector | 0.774 | 0.755 | |
  | **v2 detector** | **0.948** | **0.856** | 0.69 / 0.99 / 0.76 / 1.00 |

  This is synthetic data. Real-server precision has to be judged from moderators' `/review resolve` outcomes.

## 4. Finance insights (`services/insights.py`)

Up to two private observations, computed only from the person's own recent ledger entries and bank profile:
- the card statement is due soon;
- the card is 70%+ used;
- this week's spending is more than 2× their usual (median of the previous 4 weeks);
- they're down on games of chance this week.

These are deterministic personal-baseline statistics, not a trained model. They are shown only to that person, are
labelled with the Clairvoyance icon, and change nothing.

## 5. Server security (`ml/security/`, `docs/SECURITY.md`)

All security models are **advisory**:
- None of them can delete, time out, ban, restrict or change permissions; deterministic rules and staff do that.
- Each has its own switch (`models.*`); `RUDHABOT_SECURITY_MODELS=0` turns them all off.
- Each is refused if its feature-schema hash doesn't match the code.
- Each runs in a worker thread with a 2-second timeout.
- When any of this fails, the rules carry on alone.

Retrain and re-benchmark everything with
`python -m rudhabot.ml.security.train --data <dir> --download` (about 11 minutes on 4 cores). The full report is
`models/security/BENCHMARK.md` and `benchmark.json`.

### Data

No Rudhabot server data is used. `ml/security/datasets.py` downloads each source from its official URL and refuses a
file whose SHA-256 differs from the recorded one. Raw text is never committed (it contains other people's messages);
only trained weights are.

| source | licence | used for | examples after de-duplication |
|---|---|---|---|
| wangyuancheng/discord-phishing-scam-clean (Hugging Face) | MIT | scam + spam heads | 1,809 Discord messages, 191 scams |
| DQN-Labs/discord_scam_detection (Hugging Face) | MIT | scam + spam heads | 22 new (it is mostly the set above, oversampled) |
| UCI SMS Spam Collection | CC BY 4.0 | spam head | 5,125 SMS, 626 spam |
| UCI PhiUSIIL Phishing URL Dataset | CC BY 4.0 | URL head | 80,000 URLs (balanced sample) |
| synthetic server chat + developer-written command phrasings | own | negatives in this server's style | 2,023 |

The splits are by a hash of the normalised text, or of the registrable domain for URLs: 70% train, 10% validation,
20% test. Training positives are augmented with the obfuscations scammers use: look-alike letters, leetspeak,
invisible characters, new domains. Augmentation is never applied to validation or test data.

**A known bias in the URL data:** PhiUSIIL's legitimate URLs are mostly bare home pages. A model would learn "has a
path = phishing", which is false on Discord. So URLs are featurised from the host only.

### Message-risk model (`message-risk-v1`, 8,396,579 parameters)

**Architecture:**
- Feature hashing (crc32 into 2^18 buckets) of character 3- to 5-grams, words and bigrams, URL host tokens, and
  the deterministic link-analysis codes. The rules become features.
- A 262,144 × 32 embedding bag, then 32 → 96 → 48 → 3 heads.
- Each head is trained only on examples with its label, then Platt-calibrated.

**Training:**
- Sparse (lazy) Adam touches only the rows a batch uses.
- Token dropout of 0.5 drops half of each example's buckets per step. It was the main defence against memorising,
  and raised scam AUC from 0.924 to 0.962 in a side-by-side run.
- Every setting peaked after the first epoch, and early stopping keeps that epoch.

**Test results** (held out; 95% Wilson intervals):

| head | n | positives | AUC | avg precision | ECE | precision @0.8 | recall @0.8 |
|---|---|---|---|---|---|---|---|
| scam | 1,698 | 38 | 0.954 | 0.744 | 0.021 | 0.73 (0.56-0.85) | 0.63 (0.47-0.77) |
| spam | 1,819 | 159 | 0.983 | 0.946 | 0.015 | 0.92 (0.87-0.96) | 0.84 (0.78-0.89) |
| url | 15,745 | 7,828 | 0.918 | 0.936 | 0.044 | 0.99 (0.99-0.99) | 0.63 (0.62-0.64) |

- **Real Discord scams only** (372 messages, 38 scams): at 0.8, precision 0.80 (0.63-0.91), recall 0.63 (0.47-0.77),
  false-positive rate 1.8%.
- **The rules alone** catch **none** of these 38: their links are missing or don't trip the deterministic analysis.
  That is the model's added value.
- **At 0.97** (the bar for corroborating a deletion), scam precision is 0.94 (0.74-0.99) with recall 0.45.
- **False positives on chat:** held-out synthetic chat is flagged 0% of the time at 0.8.
- **Latency:** 0.23 ms per message.

**Limits:**
- 38 scam test messages is a small number, which is why the intervals are wide.
- All scam data comes from one public server's messages.
- Real precision on this server will only be known from moderators' verdicts, which is why the bot starts in shadow
  mode.

### Wargame simulator (`ml/security/sim.py`, `wargame.py`)

Each simulated server has:
- 1,000-2,500 members, staff with clearance tiers, a daily rhythm with quiet hours, and normal chat;
- hard negatives: announcement hype bursts, roleplay battles (rapid commands with pings), onboarding waves of real
  joins from a partner invite, birthday floods of near-identical wishes, staff clean-up bans and role restructuring;
- 8 randomised attack families: join raid, spam raid, phishing from compromised members, mention bomb, compromised
  staff nuke, malicious bot, webhook spam, and a slow-drip raid built to slip under rate rules.

Days are replayed through the **real** engine. The simulator was also a debugging tool: the first runs showed 30
innocent messages a day deleted (roleplay battles and onboarding greetings mistaken for coordination). The rules were
fixed until the test servers showed none.

### Incident model (`incident-v1`, 7,753 parameters)

**Model:** an 83-feature multi-scale activity vector (1, 5 and 15 minutes: joins, young or avatar-less joins,
duplicates, cross-account duplicates, pings, risky links, webhook messages, audit points, ...) goes into a
64 → 32 MLP. It has a binary "attack under way" head and an 8-way family head, Platt-calibrated.

**Data:** trained on 12 servers × 40 days (633,016 minutes, 3,010 under attack). Two families, `slow_drip` and
`malicious_bot`, were **held out of training** entirely.

**Results** on 6 test servers × 40 days:
- **Overall:** per-minute AUC 0.752, average precision 0.392, ECE 0.015; quiet minutes flagged 0.08%.
- **Per family:** attack minutes flagged at 0.8:

  | family | flagged |
  |---|---|
  | join raid | 98.8% |
  | spam raid | 99.0% |
  | webhook spam | 100% |
  | staff nuke | 88.9% |
  | mention bomb | 80.3% |
  | phishing | 50.3% |

- **Held-out families:** `malicious_bot`, which it never saw, still flagged 79% of minutes (its family head can't
  name it, as expected). `slow_drip` was flagged 13%: spread over hours, it barely moves per-minute counts.
- **Limits:** a confident reading opens a digest-only incident 0.62 times per quiet day. Its readings move severity
  by at most 0.15 and never the threat level.

### Alert ranker (`ranker-v1`)

**Model:** logistic regression over an incident's evidence: the best score per detector, accounts, channels,
signals, threat level, model contribution and family.

**Results** on 349 test incidents (326 real), read 5 minutes after opening:
- **Overall:** AUC 0.863, vs 0.860 for severity alone.
- **Digest-only flags:** 0.891 vs 0.843. It is better here, so it orders the digest.
- **Full alerts:** 0.707 vs 0.783. It is worse here, so alerts aren't re-ordered by it.

### Full system (6 simulated test servers × 40 days)

| attack | n | alerted (rules / +models) | median time to alert | messages removed | contained |
|---|---|---|---|---|---|
| join raid | 23 | 100% / 100% | 12 s | 92.5% (98.5% of raiders on probation) | n/a |
| spam raid | 14 | 100% / 100% | 9 s | 93.4% | n/a |
| phishing | 18 | 56% / **72%** | 0 s | 47.8% | n/a |
| mention bomb | 16 | 100% / 94% | 0 s | 100% | n/a |
| compromised staff nuke | 23 | 87% / 87% | 72 s | n/a | 87%, after a median 7 of 17 actions |
| malicious bot | 22 | 95% / 95% | 59 s | n/a | 95.5%, after a median 6 |
| webhook spam | 16 | 94% / **100%** | 6 s | 100% | n/a |
| slow drip | 19 | 100% / 100% | ~1 h | 55.1% (44% of its accounts on probation with the models, 25% without) | n/a |

Innocent members per simulated day:
- 0 messages deleted, 0 timeouts;
- 0.02-0.03 false alerts per quiet day;
- about 1 probation, from the standing orders while the threat level is raised after a real attack, which is by
  design.

**What the models add:** a confident message-model reading on a message with an unofficial link can raise an alert
for review, without any action and without moving the threat level. That lifts phishing alerting from 56% to 72%.

**What they don't do:** delete those messages. The model can only corroborate a deletion when a rule already found
a risky link.

**Not contained:** staff nukes that stayed under budget (a few bans and kicks) are alerted by the staff baseline and
the rules but not contained. That is the cost of not quarantining staff on weak evidence.

**These are simulation results.** The simulator measures behaviour against modelled attacks. It doesn't prove
real-world accuracy.

## Next steps
1. **Play:** search two plies within your own turn; more generations from v3.
2. **Prediction:** the early game is where every model is weakest; features for deck composition remaining could help.
3. **Intent:** collect consented real phrasings (for example `/ask` texts that users approve) to retrain and evaluate.
4. **Anomaly:** tune the bars from moderators' dismiss/confirm outcomes once real signals exist.
5. **Security:**
   - measure real precision from moderators' verdicts in shadow mode before going live;
   - more scam data (with a proper data policy);
   - slow-drip detection across hours (per-account history rather than per-minute counts);
   - tighter default budgets if staff agree.

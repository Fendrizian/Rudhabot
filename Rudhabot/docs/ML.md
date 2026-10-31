# Clairvoyance: Rudhabot's in-house machine learning

Clairvoyance (formerly "the Oracle") is the ML that Rudhabot trains and serves itself. It uses no hosted model and no
pretrained weights. It has three tasks:

| Task | Model | Trained on | Used for |
|---|---|---|---|
| **Cloudfall value network** | `clairvoyance-v2` slot-encoder network (175k params) | engine self-play only | AI opponent, coaching hints, live win bar, turning points, Deck Doctor, similar cards |
| **Intent router** | `intent-v1` n-gram MLP (789k params, 1 MB) | developer-written phrases | `/ask` and @mentions: map plain words to an existing command, or ask a clarifying question |
| **Anomaly monitor** | isolation forest + rules, fitted on this server's own ledger | the server's last 7 days of hourly activity windows | review signals for moderators (`/review`) |

It also produces private **finance insights** in your own `/balance` (sent privately) and in `/bank card info`. These are derived from the person's
own ledger.

**Trust boundary (all tasks):** Clairvoyance only advises.
- Game moves are applied by `engine.apply`, which re-validates them.
- Commands run through their own permission checks.
- Review signals never change balances or permissions.
- Insights never change limits, interest or charges.

## 1. Cloudfall value network

### What it predicts
P(win | state, perspective), from what that player can legitimately see. `encode()`/`encode_v2()` never read the
opponent's hand or either deck's order, and the tests prove it by scrambling hidden zones.

Before searching, the AI re-samples hidden information (determinization), so it gets no information that a human in its
seat wouldn't have.

### Architectures
- **v1 `ClairvoyanceNet`** (`oracle-v1`, kept for rollback):
  - 235 dense features plus three card-count vectors, pooled through a 16-d learned card embedding.
  - An MLP of 128 → 64 → 1. 46,465 parameters.
- **v2 `ClairvoyanceSlotNet`** (`clairvoyance-v2`, current):
  - **Slot encoder:** each of the 8 field slots is encoded by one shared encoder. Its input is the slot's 20 attributes,
    the card embedding of the unit standing there, and its attachments pooled through the same embedding (24-d). The
    encoder layer is 68 → 48 with ReLU.
  - **Trunk:** the slot encodings stay positional, because adjacency matters for Taint spread and Thunderclap. Global
    features and pooled hand/board embeddings join them in a 256 → 128 → 1 MLP.
  - 175,217 parameters, about 3.8× v1. Pure numpy, with hand-written backprop that passed a numerical gradient check.

### Training (`python -m rudhabot.ml.train --version clairvoyance-v2`)
- **Self-play and expert iteration:** 3 generations × 6,000 games, from 18,000 training seeds.
  - Generation 0 is played by ε-greedy heuristic search.
  - Generations 1 and 2 are played by the previous network, mixed with heuristic and random seats.
- **Deck diversity:** in 40% of games, each deck is a legal random mutation (3–10 swaps) of an archetype. That widens
  the distribution beyond the six starter decks.
- **TD(λ) targets:** from generation 1, targets are λ-returns with λ = 0.7. They blend the final result with the acting
  network's afterstate estimates, which lowers label variance. Evaluation always uses the **true** results.
- **Optimiser:** binary cross-entropy, L2 1e-4, Adam with a cosine schedule, and early stopping that restores the best
  validation epoch. The bigger network overfits after 1–3 epochs.
- **Calibration:** Platt scaling (a = 0.951, b = −0.088), fitted on separate calibration seeds.
- **Build:** 66 min on 4 CPU cores.
- **Disjoint seed ranges:** train 0…, calibration 8,000,000…, test 9,000,000…, arenas 5,500,000…, benchmark
  7,700,000… and 7,800,000…

### Measured results

**Playing strength.** Each arena plays every seed twice with seats swapped. The intervals are Wilson 95%. The served
policy is the "blend": logit(P_net) + 0.5·logit(P_heuristic) with 1-ply determinized search.

| Matchup | Score | 95% CI | Games |
|---|---|---|---|
| **v2 blend vs heuristic** | **66.5%** | 61.7–70.9 | 400 |
| v2 blend vs heuristic, mutated decks | 64.2% | 59.4–68.8 | 400 |
| v2 net-only vs heuristic | 62.0% | 57.2–66.6 | 400 |
| v2 net-only vs random | 96.8% | 94.5–98.1 | 400 |
| v2 blend vs v1 blend (training arena) | 55.1% | 50.2–59.9 | 400 |
| **v2 blend vs v1 blend (fresh confirmation seeds)** | **54.2%** | 50.2–58.1 | 600 |
| *for reference:* v1 blend vs heuristic (fresh seeds) | 52.3% | 47.9–56.6 | 500 |

v1 played at parity with the hand-tuned heuristic. v2 beats it clearly, including with decks it never trained on, and
edges out v1 head-to-head.

**Win-probability quality: apples-to-apples.** Both models are scored on the same 12,274 held-out states from 500
heuristic-played games, half with archetype decks and half with mutated decks:

| | log-loss ↓ | Brier ↓ | ECE ↓ | AUC ↑ |
|---|---|---|---|---|
| v1 (oracle-v1) | **0.476** | **0.159** | 0.033 | **0.852** |
| v2 (clairvoyance-v2) | 0.480 | 0.161 | **0.011** | 0.845 |
| heuristic baseline | 0.520 | 0.174 | 0.019 | 0.817 |
| v1 on mutated decks | 0.479 | 0.159 | 0.042 | 0.853 |
| v2 on mutated decks | **0.464** | **0.154** | **0.022** | **0.858** |
| v1 on archetype decks | **0.473** | **0.158** | 0.032 | **0.851** |
| v2 on archetype decks | 0.496 | 0.167 | 0.022 | 0.832 |

What this means:
- As a pure predictor, v2 is **not better than v1 overall**: v1's AUC is slightly higher.
- v2 is about 3× better calibrated.
- v2 is better on unfamiliar decks and slightly worse on the six starter decks.
- Its gain shows in **move choice and robustness**, which is what the bigger, unit-aware network was for.
- On its own harder test set (network-played, mutated decks: 13,224 states), v2 scored AUC 0.816 against the
  heuristic's 0.798. That number isn't comparable with v1's earlier 0.839, which came from an easier distribution.

**Latency** (one core): network evaluation p50 0.34 ms. A full AI move p50 1.2 ms and p95 3.7 ms. Serving runs in a
worker thread behind a semaphore and a timeout, with a heuristic fallback.

**Learned embeddings** are unsupervised. v1's were more interpretable: Wahen's nearest card was her twin Serena, and May's
were the other Fallen. v2's are noisier, because v2 also sees each unit's identity through its slot. May's nearest cards
are still Tiber and Ilyra, two other Fallen. "Similar role" is shown as an advisory hint only.

### Lifecycle and rollback
- **Artifacts:** `models/<version>.npz` stores the architecture header, weights, calibration and metadata.
  `models/manifest.json` stores, per version, the architecture, feature-schema hash, config, per-epoch history, metrics,
  arenas, latency and learned card values.
- **Safety on load:** a schema or architecture mismatch is refused, and the bot falls back to the heuristic.
- **Rollback:** `CLAIRVOYANCE_VERSION=oracle-v1`, or `/clairvoyance reload version:oracle-v1`. `CLAIRVOYANCE_ENABLED=0`
  turns the model off. The old `ORACLE_*` variable names still work.
- **Monitoring:** `/clairvoyance status` shows the version, health, metrics, arena results, call and fallback counts,
  latency, and the assistant's state.

## 2. Intent router (`python -m rudhabot.ml.intent`)

- **Task:** map a message to one of 28 intents. Each intent is an existing capability (balance, inventory, shop, loans,
  bank, stocks, each game, Cloudfall play/rules/collection, wishes, …) or "chat".
  - "chat" goes to the OpenAI assistant, or to the local docs.
  - A confident intent (≥ 60%) is offered as a button that runs the **real** command, with its own checks, for the
    person who asked only.
  - At 25–60% confidence, Rudhabot asks which of the top options was meant.
  - Commands that need details (amounts, bets) are explained, not guessed.
- **Model:** hashed character 2–5-grams plus word uni/bi-grams form an 8,192-d TF vector, then a 96-unit ReLU layer and a
  softmax. Confidence is temperature-calibrated (T = 1.2, fitted on held-out seed phrases).
- **Data:** developer-written phrases (`ml/intent_data.py`), augmented with politeness, casing and typo variants: 2,538
  training examples. The **test set is 58 separately written phrasings**, never trained on or augmented.
- **Held-out results:**

  | Metric | Value |
  |---|---|
  | Accuracy | 81.0% |
  | Macro-F1 | 0.797 |
  | ECE | 0.091 |
  | Answers at ≥ 60% confidence | 81% of messages, 91.5% accurate |

  The misses are listed in `models/intent-v1.json`. Examples: "what's in my backpack" → balance, and "i want to invest in
  joot" → inventory.

  I deliberately did **not** add training phrases for those exact misses, so the test set stays a fair estimate. The
  honest next step is real, consented usage data.

## 3. Anomaly monitor (`ml/anomaly.py`, `services/review.py`)

- **Features:** per-user hourly windows built from the audit ledger and the failed-interaction log:
  - entries;
  - money in and money out;
  - largest single change;
  - net change;
  - distinct sources;
  - gambling wins;
  - transfers received from other people;
  - failed interactions.
- **Model:** an isolation forest (numpy, 100 trees × 256 samples), refitted on the server's own last 7 days at startup and
  every 6 h, so "unusual" means unusual *for this server*. Each flag lists the features that deviate most from the
  population (robust z-scores).
- **Rules:** hard rules also apply during cold start, before 120 windows exist: 15+ failures in an hour, a single change
  of 1,000,000 or more, or 12+ transfers in from other people.
- **Rate limits:** each user's current hour is re-judged at most once a minute, and only one open signal per user is
  allowed per 6 h.
- **Output:** a signal is stored in `reviews.json` and posted to the review channel. A moderator dismisses or confirms it
  with `/review resolve`. **Nothing else happens.**
- **Synthetic benchmark** (`python -m rudhabot.ml.anomaly`): ordinary simulated players plus 30 injected anomalies of four
  kinds (money mule, implausible win streak, failure storm, one huge movement), over 5 seeds of about 3,770 windows each.
  Results: precision 0.957, recall 0.873. This is synthetic data. Real-server precision has to be judged from moderators'
  `/review resolve` outcomes.

## 4. Finance insights (`services/insights.py`)

Up to two private observations, computed only from the person's own ledger and bank profile:
- the card statement is due soon;
- the card is 70%+ used;
- this week's spending is more than 2× their usual (median of the previous 4 weeks);
- they're down on games of chance this week;

These are deterministic personal-baseline statistics, not a trained model. They are shown only to that person, are
labelled "💡 Clairvoyance", and change nothing.

## Next steps
1. **Play:** add a 2-ply search within your own turn, and more generations. v2's training curve was still improving its
   play.
2. **Prediction:** add an ensemble or v1-style pooled features to v2 to recover v1's edge on archetype decks.
3. **Intent:** collect consented real phrasings (for example, log `/ask` texts that users approve) to retrain and
   evaluate the router.
4. **Anomaly:** tune the threshold from moderators' dismiss/confirm outcomes once real signals exist.

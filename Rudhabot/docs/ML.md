# Clairvoyance: Rudhabot's in-house machine learning

Clairvoyance (formerly "the Oracle") is the ML that Rudhabot trains and serves itself. It uses no hosted model and no
pretrained weights, and no user data: the Cloudfall model learns only from engine self-play, the intent router only from
developer-written phrases, and the anomaly monitor fits the server's own audit ledger at run time.

| Task | Model (served) | Trained on | Used for |
|---|---|---|---|
| **Cloudfall value network** | `clairvoyance-v3`: slot-encoder network, 175k params | engine self-play only | AI opponent, coaching hints, the live win bar, turning points, Deck Doctor, similar cards |
| **Intent router** | `intent-v2`: hashed n-gram MLP, 30 intents | developer-written phrases | `/ask` and @mentions: map plain words to an existing command, or ask a clarifying question |
| **Anomaly monitor** | isolation forest + personal baselines + rules | the server's last 7 days of hourly activity windows | review signals for moderators (`/review`) |

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
4. **EMA (Polyak) weight averaging**, decay 0.999; early stopping keeps the best validation epoch. (v2 overfit after
   1–3 epochs; v3's validation loss kept improving longer.)
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
| One evaluation / one AI move (p50, one thread) | 0.026 / 0.43 ms | 0.056 / 0.64 ms | 0.054 / 0.58 ms |

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

INTENT_RESULTS_TABLE

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

## Next steps
1. **Play:** search two plies within your own turn; more generations from v3.
2. **Prediction:** the early game is where every model is weakest; features for deck composition remaining could help.
3. **Intent:** collect consented real phrasings (for example `/ask` texts that users approve) to retrain and evaluate.
4. **Anomaly:** tune the bars from moderators' dismiss/confirm outcomes once real signals exist.

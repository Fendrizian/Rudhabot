# The Cloudfall Clairvoyance: Rudhabot's in-house model

The Clairvoyance is a compact neural network that Rudhabot **trains and serves itself**. It learns to read Asgrelia: Cloudfall
positions. It uses no hosted model and no pretrained weights. Its only training data is games the bot's own rules engine plays
against itself.

## What it does (and what it may not do)

| Product | What the model provides | What trusted code decides |
|---|---|---|
| **AI opponent** (`/tcg practice`) | A score for the state each *legal* move leads to; the policy picks one | The engine generates the legal moves and `engine.apply` re-validates and resolves the chosen one |
| **Coach** (🔮 button, coached/practice matches only) | Top 3 legal moves with calibrated win % and the change from now | Coaching is off in rated games; the hints are shown only to the player whose decision it is |
| **Live win bar** (every board) | P(player 0 wins), averaged over both seats with **both hands hidden** | Nothing; it's purely informational |
| **Turning points + Chronicle** | The moves with the largest swing in the public win estimate | The facts the recap is written from (winner, rounds, reason) come from the engine |
| **Deck Doctor** (`/tcg deck analyze`) | It pilots your deck against all six archetypes; it also suggests swaps by learned card value | Swaps are only suggested if they keep the deck legal *and* you own the cards; nothing is changed for you |
| **Similar cards** (`/tcg card`) | Nearest neighbours in the learned card-embedding space | Nothing |

The model **never** mutates game state, balances, collections, ratings or rewards. It can only rank moves that the engine
already deems legal. Fairness: before scoring, hidden information is **re-sampled (determinization)**. The searcher's own
deck order and the opponent's hand and deck are shuffled from what that seat could know, so the AI gets no information that a
human in its seat wouldn't have. `encode()` itself never reads the opponent's hand or either deck's order, and `test_ml`
proves this by scrambling hidden zones.

## Model

- **Input** (`rudhabot/ml/features.py`, schema hash `features.schema_hash()`):
  - 235 dense features: Threat Level, Protocol, Sky, phase, both players' public counters, 8 field slots × 20 attributes,
    and a 4-value Clash preview computed by the engine.
  - Three card-count vectors over the 112 playable cards: own hand, own board, enemy board.
- **Architecture** (`rudhabot/ml/nn.py`, pure numpy):
  - A shared **learned card embedding** `E ∈ ℝ^{112×16}` pools each card-count vector.
  - The pooled vectors and the dense features feed an MLP (235 + 48 → 128 → 64 → 1) with ReLU activations and a sigmoid output.
  - **46,465 parameters**, a 173 KB artifact.
- **Training:**
  - Loss: binary cross-entropy (draws are 0.5 targets), plus L2 weight decay 1e-4.
  - Optimiser: Adam with a cosine learning-rate schedule.
  - Early stopping on validation loss restores the best epoch. One game's states are highly correlated, so the network
    overfits after about 1–3 epochs.
- **Calibration:** Platt scaling (a, b) = (0.781, −0.036), fitted on a **separate** set of calibration games, never the test set.
- **Move policy served:** a 1-ply determinized afterstate search. Each candidate is scored by
  `logit(P_net) + 0.5 · logit(P_heuristic)` (see "Playing strength"). Everything that is *displayed* as a probability uses
  the calibrated network alone.

## Data and governance

- **Source:** engine self-play only, with no Discord messages and no user data. Each decision point is recorded with probability 0.12, from
  both players' perspectives, and labelled with the eventual result.
- **Policies used to generate data:**
  - Generation 0: heuristic search (ε = 0.1).
  - Generations 1–2: the previous network (ε = 0.1), mixed with heuristic and random seats for diversity (expert iteration).
- **Leakage control:** train seeds (0…), calibration seeds (8,000,000…), test seeds (9,000,000…) and arena seeds
  (5,000,000… and 6/7,000,000…) never overlap. The split is by game, never by state.
- **Personalisation:** none. No user data is used to train or adapt the model. If owner-approved, opt-in human-match logging is
  added later, it must be consented, anonymised and kept apart from the self-play set (see "Next steps").

## Measured results: oracle-v1

All numbers come from `models/manifest.json`, produced by `python -m rudhabot.ml.train --version oracle-v1`. The build took
425 s on 4 CPU cores.

**Win-probability quality**, on 11,240 held-out states from 500 test games:

| | log-loss ↓ | Brier ↓ | ECE ↓ | AUC ↑ |
|---|---|---|---|---|
| **Clairvoyance (calibrated)** | **0.490** | **0.164** | **0.018** | **0.839** |
| Clairvoyance (uncalibrated) | 0.493 | 0.164 | 0.022 | 0.839 |
| Heuristic baseline | 0.626 | 0.205 | 0.066 | 0.757 |
| Constant 0.5 | 0.693 | 0.250 | — | 0.500 |

The Clairvoyance is clearly a better estimator than the hand-written baseline. Its AUC is +0.08 higher, and its calibration error
is a quarter of the baseline's.

**Playing strength.** Each arena plays every seed twice with seats swapped, and intervals are Wilson 95%:

| Matchup | Score | 95% CI | Games |
|---|---|---|---|
| Net-only search vs heuristic search | 48.7% | 43.1–54.3% | 300 |
| Net-only vs random | 94.3% | 91.1–96.4% | 300 |
| Heuristic vs random (reference) | 94.7% | 91.5–96.7% | 300 |
| Blend (w=0.5) vs heuristic (selection seeds) | 57.0% | 51.3–62.5% | 300 |
| **Blend (w=0.5) vs heuristic (fresh confirmation seeds)** | **52.3%** | 47.9–56.6% | 500 |
| **Blend vs net-only (fresh seeds)** | **59.3%** | 53.7–64.7% | 300 |
| Blend vs random (fresh seeds) | 98.0% | 95.0–99.2% | 200 |

The blend weight was picked from {0.5, 1.0} on the selection seeds, then confirmed on untouched seeds.

What this does and doesn't show:
- The served blend is **reliably stronger than the network alone**.
- On fresh seeds it is **statistically indistinguishable from the tuned heuristic baseline**.
- I am not claiming that the model plays better than the baseline. The measured gain is in judging positions
  (calibrated, discriminative win probability), which is what the coach, the win bar, turning points and Deck Doctor use.

**Latency** (one core, oracle-v1):
- One network evaluation: p50 0.03 ms, p95 0.04 ms.
- A full AI move (determinize, apply each legal move, batch-score): p50 0.6 ms, p95 1.8 ms, mean branching 4.2.
- Deck Doctor (24 games): about 3 s.
- Serving runs in `asyncio.to_thread` behind a semaphore (2) with a timeout of 4 s (60 s for Deck Doctor), so it never
  blocks the Discord event loop.

**Learned embeddings.** These are qualitative and unsupervised; the model was never told any synergies. Wahen Nirvana's
nearest card is her twin and Bond partner Serena Nirvana, and May Selunara's nearest are the other Fallen (Tiber, Ilyra). Some
neighbourhoods are noisy, so the UI words this as "plays a similar role" and treats it as advisory.

## Lifecycle

```bash
python -m rudhabot.ml.train --version clairvoyance-v2           # self-play → train → calibrate → evaluate → register
```

```bash
python -m rudhabot.ml.evaluate --games 150                # re-measure the current artifact
```

- **Artifacts:** `models/<version>.npz` holds the weights, calibration and metadata. `models/manifest.json` holds, per
  version, the file, schema hash, config, data description, per-epoch history, metrics, strength, latency and learned card
  values. The artifact is small (173 KB), so it is committed. It can also be rebuilt from seeds.
- **Safety on load:** an artifact whose schema hash differs from the running feature code is refused, and the bot falls back
  to the heuristic. A missing or corrupt file, an exception or a timeout all fall back too, and the fallback is counted.
- **Rollback:**
  - Set `CLAIRVOYANCE_VERSION=oracle-v1`, or run `/clairvoyance reload version:oracle-v1`.
  - Or set `"current"` in the manifest.
  - `CLAIRVOYANCE_ENABLED=0` disables the model entirely; the heuristic AI keeps every feature working.
- **Monitoring:** `/clairvoyance status` (admin) shows the version, load errors, held-out metrics, arena results, per-task call
  and fallback counts, and live p50/p95 latency. Logs record task, model version and latency, never message content.

## Limitations

- Play is 1-ply. The value net has to judge mid-turn states, which caps tactical depth. The AI plays at about baseline
  strength.
- The training distribution is the six archetype decks. Custom decks are out of distribution, so estimates for them are
  rougher.
- Deck Doctor's samples are small: 4 games per matchup in about 3 s. It's a direction, not a verdict, and the UI says so.

## Next steps (highest value first)

1. **TD(λ) or bootstrapped targets plus a 2-ply search inside your own turn.** This is the most likely route to beating the
   heuristic in play.
2. **More archetype variety in self-play,** by randomly mutating the six decks. This would cover custom decks.
3. **Opt-in human data.** It needs an owner decision and consent text: log anonymised states from rated matches whose players
   opted in, keep retention short, and keep it separate from the self-play set.

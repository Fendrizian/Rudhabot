# Clairvoyance benchmark (2026-11-05T08:43:49+00:00)

Every model is scored on the same held-out states against the true game outcome. Lower log-loss / Brier / ECE is better; higher AUC is better. *Public* = own hand hidden (the spectator win bar).

## States from heuristic-played games

| Subset | Model | log-loss | Brier | ECE | AUC | public log-loss | public AUC |
|---|---|---|---|---|---|---|---|
| all (12,274 states, 500 games) | heuristic | 0.520 | 0.174 | 0.019 | 0.817 | | |
| | clairvoyance-v3 | 0.470 | 0.157 | 0.008 | 0.852 | 0.481 | 0.842 |
| | clairvoyance-v4 | 0.469 | 0.157 | 0.009 | 0.852 | 0.479 | 0.843 |
| archetype_decks (6,234 states, 250 games) | heuristic | 0.517 | 0.173 | 0.021 | 0.819 | | |
| | clairvoyance-v3 | 0.481 | 0.162 | 0.025 | 0.843 | 0.489 | 0.836 |
| | clairvoyance-v4 | 0.477 | 0.160 | 0.017 | 0.845 | 0.485 | 0.838 |
| mutated_decks (6,040 states, 250 games) | heuristic | 0.522 | 0.175 | 0.018 | 0.814 | | |
| | clairvoyance-v3 | 0.458 | 0.152 | 0.015 | 0.862 | 0.472 | 0.850 |
| | clairvoyance-v4 | 0.461 | 0.153 | 0.026 | 0.861 | 0.473 | 0.849 |
| rounds_1_3 (2,530 states, 468 games) | heuristic | 0.685 | 0.246 | 0.049 | 0.578 | | |
| | clairvoyance-v3 | 0.649 | 0.229 | 0.020 | 0.664 | 0.671 | 0.605 |
| | clairvoyance-v4 | 0.651 | 0.230 | 0.023 | 0.662 | 0.670 | 0.606 |
| rounds_4_7 (5,514 states, 496 games) | heuristic | 0.541 | 0.181 | 0.014 | 0.804 | | |
| | clairvoyance-v3 | 0.484 | 0.160 | 0.013 | 0.847 | 0.496 | 0.838 |
| | clairvoyance-v4 | 0.482 | 0.159 | 0.014 | 0.848 | 0.493 | 0.839 |
| rounds_8_plus (4,230 states, 367 games) | heuristic | 0.393 | 0.122 | 0.020 | 0.906 | | |
| | clairvoyance-v3 | 0.344 | 0.110 | 0.017 | 0.927 | 0.347 | 0.925 |
| | clairvoyance-v4 | 0.344 | 0.109 | 0.019 | 0.927 | 0.347 | 0.925 |

## States from model-played games

| Subset | Model | log-loss | Brier | ECE | AUC | public log-loss | public AUC |
|---|---|---|---|---|---|---|---|
| all (15,024 states, 500 games) | heuristic | 0.591 | 0.196 | 0.069 | 0.776 | | |
| | clairvoyance-v3 | 0.522 | 0.174 | 0.045 | 0.822 | 0.531 | 0.815 |
| | clairvoyance-v4 | 0.516 | 0.172 | 0.032 | 0.823 | 0.524 | 0.817 |
| archetype_decks (7,670 states, 250 games) | heuristic | 0.558 | 0.186 | 0.059 | 0.795 | | |
| | clairvoyance-v3 | 0.505 | 0.168 | 0.035 | 0.833 | 0.512 | 0.827 |
| | clairvoyance-v4 | 0.497 | 0.166 | 0.026 | 0.835 | 0.503 | 0.830 |
| mutated_decks (7,354 states, 250 games) | heuristic | 0.625 | 0.205 | 0.079 | 0.755 | | |
| | clairvoyance-v3 | 0.540 | 0.180 | 0.056 | 0.810 | 0.550 | 0.801 |
| | clairvoyance-v4 | 0.536 | 0.178 | 0.043 | 0.810 | 0.545 | 0.802 |
| rounds_1_3 (2,582 states, 470 games) | heuristic | 0.711 | 0.256 | 0.081 | 0.531 | | |
| | clairvoyance-v3 | 0.661 | 0.232 | 0.019 | 0.647 | 0.678 | 0.603 |
| | clairvoyance-v4 | 0.663 | 0.233 | 0.013 | 0.642 | 0.677 | 0.602 |
| rounds_4_7 (5,566 states, 497 games) | heuristic | 0.616 | 0.210 | 0.068 | 0.741 | | |
| | clairvoyance-v3 | 0.545 | 0.181 | 0.039 | 0.805 | 0.554 | 0.797 |
| | clairvoyance-v4 | 0.535 | 0.178 | 0.024 | 0.810 | 0.545 | 0.801 |
| rounds_8_plus (6,876 states, 406 games) | heuristic | 0.525 | 0.161 | 0.065 | 0.848 | | |
| | clairvoyance-v3 | 0.452 | 0.146 | 0.066 | 0.882 | 0.456 | 0.879 |
| | clairvoyance-v4 | 0.446 | 0.144 | 0.052 | 0.881 | 0.449 | 0.879 |

## Paired differences (A − B, 95% game-level bootstrap)

| A vs B | source | Δ log-loss | Δ Brier | Δ AUC |
|---|---|---|---|---|
| clairvoyance-v3 vs clairvoyance-v4 | heuristic_play | +0.0006 [-0.0028, +0.0044] | +0.0004 [-0.0009, +0.0017] | -0.0005 [-0.0028, +0.0017] |
| clairvoyance-v3 vs clairvoyance-v4 | model_play | +0.0060 [+0.0002, +0.0122] | +0.0019 [+0.0001, +0.0038] | -0.0014 [-0.0045, +0.0020] |
| clairvoyance-v3@public vs clairvoyance-v4@public | heuristic_play | +0.0015 [-0.0021, +0.0054] | +0.0007 [-0.0006, +0.0022] | -0.0006 [-0.0031, +0.0016] |

## Playing strength (seat-swapped arenas, Wilson 95%)

| Matchup | Score | 95% CI | Games |
|---|---|---|---|
| clairvoyance-v3 vs heuristic | 72.0% | 68.3–75.4 | 600 |
| clairvoyance-v3 vs heuristic (mutated decks) | 74.2% | 70.6–77.6 | 600 |
| clairvoyance-v3 vs random | 99.0% | 97.8–99.5 | 600 |
| clairvoyance-v4 vs heuristic | 74.9% | 71.3–78.2 | 600 |
| clairvoyance-v4 vs heuristic (mutated decks) | 74.8% | 71.1–78.1 | 600 |
| clairvoyance-v4 vs random | 98.0% | 96.5–98.9 | 600 |
| clairvoyance-v3 vs clairvoyance-v4 | 44.2% | 40.2–48.2 | 600 |

## Search depth (same network, seat-swapped, Wilson 95%)

| Matchup | Score | 95% CI | Games |
|---|---|---|---|
| clairvoyance-v3: lookahead (top 5) vs 1-ply | 62.5% | 58.6–66.3 | 600 |
| clairvoyance-v4: lookahead (top 5) vs 1-ply | 57.7% | 53.7–61.6 | 600 |

## Latency (single thread)

| Model | eval p50 | eval p95 | 1-ply move p50 | p95 | lookahead move p50 | p95 |
|---|---|---|---|---|---|---|
| clairvoyance-v3 | 0.098 ms | 0.189 ms | 1.14 ms | 2.81 ms | 3.1 ms | 25.3 ms |
| clairvoyance-v4 | 0.136 ms | 0.194 ms | 1.13 ms | 2.84 ms | 3.4 ms | 24.2 ms |

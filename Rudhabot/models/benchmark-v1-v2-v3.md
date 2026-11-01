# Clairvoyance benchmark (2026-11-01T03:44:48+00:00)

Every model is scored on the same held-out states against the true game outcome. Lower log-loss / Brier / ECE is better; higher AUC is better. *Public* = own hand hidden (the spectator win bar).

## States from heuristic-played games

| Subset | Model | log-loss | Brier | ECE | AUC | public log-loss | public AUC |
|---|---|---|---|---|---|---|---|
| all (12,274 states, 500 games) | heuristic | 0.520 | 0.174 | 0.019 | 0.817 | | |
| | oracle-v1 | 0.476 | 0.159 | 0.033 | 0.852 | 0.493 | 0.836 |
| | clairvoyance-v2 | 0.480 | 0.161 | 0.011 | 0.845 | 0.493 | 0.833 |
| | clairvoyance-v3 | 0.470 | 0.157 | 0.008 | 0.852 | 0.481 | 0.842 |
| archetype_decks (6,234 states, 250 games) | heuristic | 0.517 | 0.173 | 0.021 | 0.819 | | |
| | oracle-v1 | 0.473 | 0.158 | 0.032 | 0.851 | 0.485 | 0.842 |
| | clairvoyance-v2 | 0.496 | 0.167 | 0.022 | 0.832 | 0.505 | 0.823 |
| | clairvoyance-v3 | 0.481 | 0.162 | 0.025 | 0.843 | 0.489 | 0.836 |
| mutated_decks (6,040 states, 250 games) | heuristic | 0.522 | 0.175 | 0.018 | 0.814 | | |
| | oracle-v1 | 0.479 | 0.159 | 0.042 | 0.853 | 0.503 | 0.831 |
| | clairvoyance-v2 | 0.464 | 0.154 | 0.022 | 0.858 | 0.480 | 0.844 |
| | clairvoyance-v3 | 0.458 | 0.152 | 0.015 | 0.862 | 0.472 | 0.850 |
| rounds_1_3 (2,530 states, 468 games) | heuristic | 0.685 | 0.246 | 0.049 | 0.578 | | |
| | oracle-v1 | 0.648 | 0.229 | 0.047 | 0.678 | 0.698 | 0.599 |
| | clairvoyance-v2 | 0.650 | 0.229 | 0.027 | 0.668 | 0.678 | 0.604 |
| | clairvoyance-v3 | 0.649 | 0.229 | 0.020 | 0.664 | 0.671 | 0.605 |
| rounds_4_7 (5,514 states, 496 games) | heuristic | 0.541 | 0.181 | 0.014 | 0.804 | | |
| | oracle-v1 | 0.495 | 0.164 | 0.037 | 0.841 | 0.515 | 0.825 |
| | clairvoyance-v2 | 0.494 | 0.164 | 0.021 | 0.840 | 0.507 | 0.830 |
| | clairvoyance-v3 | 0.484 | 0.160 | 0.013 | 0.847 | 0.496 | 0.838 |
| rounds_8_plus (4,230 states, 367 games) | heuristic | 0.393 | 0.122 | 0.020 | 0.906 | | |
| | oracle-v1 | 0.348 | 0.109 | 0.029 | 0.927 | 0.343 | 0.929 |
| | clairvoyance-v2 | 0.361 | 0.116 | 0.023 | 0.918 | 0.362 | 0.918 |
| | clairvoyance-v3 | 0.344 | 0.110 | 0.017 | 0.927 | 0.347 | 0.925 |

## States from model-played games

| Subset | Model | log-loss | Brier | ECE | AUC | public log-loss | public AUC |
|---|---|---|---|---|---|---|---|
| all (13,446 states, 500 games) | heuristic | 0.510 | 0.167 | 0.018 | 0.831 | | |
| | oracle-v1 | 0.468 | 0.155 | 0.038 | 0.858 | 0.482 | 0.847 |
| | clairvoyance-v2 | 0.447 | 0.148 | 0.025 | 0.871 | 0.459 | 0.861 |
| | clairvoyance-v3 | 0.439 | 0.145 | 0.015 | 0.875 | 0.449 | 0.866 |
| archetype_decks (6,688 states, 250 games) | heuristic | 0.479 | 0.160 | 0.019 | 0.846 | | |
| | oracle-v1 | 0.451 | 0.148 | 0.049 | 0.873 | 0.463 | 0.862 |
| | clairvoyance-v2 | 0.432 | 0.142 | 0.030 | 0.881 | 0.444 | 0.871 |
| | clairvoyance-v3 | 0.416 | 0.136 | 0.024 | 0.889 | 0.428 | 0.879 |
| mutated_decks (6,758 states, 250 games) | heuristic | 0.541 | 0.175 | 0.025 | 0.817 | | |
| | oracle-v1 | 0.485 | 0.162 | 0.033 | 0.843 | 0.500 | 0.833 |
| | clairvoyance-v2 | 0.462 | 0.153 | 0.031 | 0.860 | 0.474 | 0.851 |
| | clairvoyance-v3 | 0.462 | 0.153 | 0.023 | 0.861 | 0.470 | 0.854 |
| rounds_1_3 (2,478 states, 462 games) | heuristic | 0.688 | 0.247 | 0.066 | 0.565 | | |
| | oracle-v1 | 0.632 | 0.221 | 0.076 | 0.719 | 0.682 | 0.651 |
| | clairvoyance-v2 | 0.624 | 0.217 | 0.051 | 0.718 | 0.655 | 0.649 |
| | clairvoyance-v3 | 0.630 | 0.220 | 0.045 | 0.703 | 0.654 | 0.639 |
| rounds_4_7 (5,922 states, 499 games) | heuristic | 0.531 | 0.178 | 0.008 | 0.810 | | |
| | oracle-v1 | 0.492 | 0.162 | 0.044 | 0.845 | 0.510 | 0.830 |
| | clairvoyance-v2 | 0.472 | 0.155 | 0.025 | 0.856 | 0.486 | 0.846 |
| | clairvoyance-v3 | 0.468 | 0.153 | 0.017 | 0.859 | 0.479 | 0.850 |
| rounds_8_plus (5,046 states, 404 games) | heuristic | 0.399 | 0.115 | 0.023 | 0.908 | | |
| | oracle-v1 | 0.359 | 0.115 | 0.026 | 0.920 | 0.350 | 0.925 |
| | clairvoyance-v2 | 0.330 | 0.104 | 0.017 | 0.933 | 0.330 | 0.934 |
| | clairvoyance-v3 | 0.312 | 0.098 | 0.014 | 0.941 | 0.314 | 0.940 |

## Paired differences (A − B, 95% game-level bootstrap)

| A vs B | source | Δ log-loss | Δ Brier | Δ AUC |
|---|---|---|---|---|
| oracle-v1 vs clairvoyance-v2 | heuristic_play | -0.0043 [-0.0235, +0.0122] | -0.0023 [-0.0100, +0.0046] | +0.0072 [-0.0068, +0.0228] |
| oracle-v1 vs clairvoyance-v2 | model_play | +0.0210 [+0.0055, +0.0387] | +0.0078 [+0.0014, +0.0149] | -0.0124 [-0.0269, +0.0009] |
| oracle-v1@public vs clairvoyance-v2@public | heuristic_play | +0.0009 [-0.0167, +0.0180] | +0.0005 [-0.0066, +0.0074] | +0.0028 [-0.0111, +0.0175] |
| oracle-v1 vs clairvoyance-v3 | heuristic_play | +0.0061 [-0.0130, +0.0250] | +0.0018 [-0.0053, +0.0090] | -0.0001 [-0.0140, +0.0137] |
| oracle-v1 vs clairvoyance-v3 | model_play | +0.0287 [+0.0088, +0.0493] | +0.0107 [+0.0034, +0.0187] | -0.0165 [-0.0317, -0.0025] |
| oracle-v1@public vs clairvoyance-v3@public | heuristic_play | +0.0128 [-0.0051, +0.0299] | +0.0052 [-0.0016, +0.0118] | -0.0061 [-0.0189, +0.0068] |
| clairvoyance-v2 vs clairvoyance-v3 | heuristic_play | +0.0105 [+0.0023, +0.0190] | +0.0040 [+0.0012, +0.0069] | -0.0073 [-0.0131, -0.0020] |
| clairvoyance-v2 vs clairvoyance-v3 | model_play | +0.0077 [-0.0012, +0.0160] | +0.0029 [-0.0002, +0.0059] | -0.0041 [-0.0093, +0.0010] |
| clairvoyance-v2@public vs clairvoyance-v3@public | heuristic_play | +0.0119 [+0.0043, +0.0198] | +0.0047 [+0.0021, +0.0075] | -0.0089 [-0.0142, -0.0040] |

## Playing strength (seat-swapped arenas, Wilson 95%)

| Matchup | Score | 95% CI | Games |
|---|---|---|---|
| oracle-v1 vs heuristic | 56.7% | 52.7–60.6 | 600 |
| oracle-v1 vs heuristic (mutated decks) | 57.2% | 53.2–61.1 | 600 |
| oracle-v1 vs random | 97.1% | 95.4–98.2 | 600 |
| clairvoyance-v2 vs heuristic | 60.3% | 56.4–64.2 | 600 |
| clairvoyance-v2 vs heuristic (mutated decks) | 63.7% | 59.8–67.5 | 600 |
| clairvoyance-v2 vs random | 96.7% | 94.9–97.8 | 600 |
| clairvoyance-v3 vs heuristic | 63.7% | 59.7–67.4 | 600 |
| clairvoyance-v3 vs heuristic (mutated decks) | 65.8% | 61.9–69.5 | 600 |
| clairvoyance-v3 vs random | 98.2% | 96.7–99.0 | 600 |
| oracle-v1 vs clairvoyance-v2 | 45.8% | 41.9–49.8 | 600 |
| oracle-v1 vs clairvoyance-v3 | 46.0% | 42.1–50.0 | 600 |
| clairvoyance-v2 vs clairvoyance-v3 | 46.9% | 43.0–50.9 | 600 |

## Latency (single thread)

| Model | eval p50 | eval p95 | move p50 | move p95 |
|---|---|---|---|---|
| oracle-v1 | 0.019 ms | 0.034 ms | 0.38 ms | 1.15 ms |
| clairvoyance-v2 | 0.061 ms | 0.129 ms | 0.61 ms | 1.85 ms |
| clairvoyance-v3 | 0.057 ms | 0.122 ms | 0.60 ms | 1.74 ms |

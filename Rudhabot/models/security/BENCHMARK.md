# Security models benchmark (2026-11-04T07:28:14+00:00)

All numbers come from `python -m rudhabot.ml.security.train`. The message model is tested on held-out public data. The incident model, ranker and full system are tested on *simulated* servers. Simulation measures behaviour against modelled attacks; it doesn't prove real-world accuracy, so the bot starts in shadow mode.

## Message-risk model (message-risk-v1, 8,396,579 parameters)

| head | test n | positives | AUC | avg precision | ECE | precision @0.8 (95% CI) | recall @0.8 (95% CI) |
|---|---|---|---|---|---|---|---|
| scam | 1698 | 38 | 0.954 | 0.744 | 0.021 | 0.727 (0.56-0.85) | 0.632 (0.47-0.77) |
| spam | 1819 | 159 | 0.983 | 0.946 | 0.015 | 0.924 (0.87-0.96) | 0.843 (0.78-0.89) |
| url | 15745 | 7828 | 0.918 | 0.936 | 0.044 | 0.992 (0.99-0.99) | 0.627 (0.62-0.64) |

Discord scam test messages (374, 38 scams): rules alone catch 0% at precision 0%; the model alone 63% at 77%; either 63% at 77%.
Held-out synthetic chat flagged at >= 0.8: scam 0.00%, spam 0.00%. Latency 0.308 ms per message.

## Incident model (incident-v1, 7,753 parameters)

Per-minute test AUC 0.752, average precision 0.392, ECE 0.015; quiet minutes flagged 0.08%. Held-out families: slow_drip, malicious_bot.

| family | held out | attack minutes | minutes flagged @0.8 | family correct |
|---|---|---|---|---|
| join_raid |  | 240 | 98.8% | 99.2% |
| spam_raid |  | 97 | 99.0% | 99.0% |
| phishing |  | 489 | 50.3% | 99.4% |
| mention_bomb |  | 61 | 80.3% | 83.6% |
| staff_nuke |  | 153 | 88.9% | 96.1% |
| malicious_bot | yes | 181 | 79.0% | 0.0% |
| webhook_spam |  | 75 | 100.0% | 100.0% |
| slow_drip | yes | 7344 | 13.0% | 0.0% |

## Alert ranker

On 349 test incidents (326 real, 289 alerted): AUC 0.863 vs 0.860 for severity alone; among digest-only flags 0.891 vs 0.843; among alerts 0.707 vs 0.783.

## Full system on 6 simulated test servers x 40 days

| family | n | alerted (rules) | alerted (+models) | median latency s | messages deleted | contained | actions before containment |
|---|---|---|---|---|---|---|---|
| join_raid | 23 | 100% | 100% | 12 | 92.5% | n/a | n/a |
| spam_raid | 14 | 100% | 100% | 9 | 93.4% | n/a | n/a |
| phishing | 18 | 56% | 72% | 0 | 47.8% | n/a | n/a |
| mention_bomb | 16 | 100% | 94% | 0 | 100.0% | n/a | n/a |
| staff_nuke | 23 | 87% | 87% | 72 | n/a | 87.0% | 7 |
| malicious_bot | 22 | 95% | 95% | 59 | n/a | 95.5% | 6 |
| webhook_spam | 16 | 94% | 100% | 6 | 100.0% | n/a | n/a |
| slow_drip | 19 | 100% | 100% | 3580 | 55.1% | n/a | n/a |

Per simulated day (rules only / rules + models):

- false alerts: 0.04 / 0.05
- probation innocent: 1.03 / 1.14
- proposal pause invites benign: 0.00 / 0.00
- false alerts per quiet day: 0.02 / 0.03
- incident-model false episodes per quiet day: 0.622

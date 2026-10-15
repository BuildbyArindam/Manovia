# Day 37 - Personalised intervention policy evaluation

**Status:** synthetic research only; not clinically validated or enabled in the product.

- Config: `day37-jitai-contextual-bandits`; seed base `3701`; independent seeds `20`.
- Synthetic in-memory interactions: `108800`; SHA-256 `88b0e9af393502abf7d7327e4ed6924b4f8a31dd4326a09873e76f63ebf4ffc7`.
- Curated policy arms: `11`; safety cap: at most 20% exploratory suggestions, never consecutive.
- Reward: bounded pre/post 1-5 rating improvement; a small penalty applies only to a started abandoned session. A skip is missing, not failure.
- No message text, personal data, time-in-app or streak signal was used.

## Online simulation - cumulative reward and regret

Each seed simulates 12 users x 60 decisions per configured policy. CIs are 95% normal-approximation intervals across independent seeds.

| Policy | Cumulative reward, mean [95% CI] | Cumulative regret, mean [95% CI] | Regret / decision: early -> late |
| --- | ---: | ---: | ---: |
| `random` | -73.388 [-76.016, -70.759] | 354.908 [352.823, 356.994] | 0.4830 -> 0.5004 |
| `epsilon_greedy` | 168.710 [157.803, 179.617] | 115.455 [103.145, 127.765] | 0.3390 -> 0.0713 |
| `linucb` | 193.795 [186.693, 200.897] | 84.867 [78.846, 90.888] | 0.2374 -> 0.0670 |
| `thompson_sampling` | 155.293 [147.573, 163.012] | 125.985 [117.440, 134.530] | 0.3390 -> 0.1002 |

The `random` row is an **offline-only, unconstrained reference** and is not eligible for serving. The other three policies pass through the hard safety wrapper; exploratory suggestions are rate-limited per synthetic user.

## Offline evaluation on logged uniform-behavior data

The posterior-mean target is trained on a disjoint training portion. Held-out behavior logs record `p(a|x) = 1 / number_of_arms`; IPS and doubly robust (DR) are evaluated against the simulator's expected target-policy reward. Evaluation outcomes do not train the model.

| Estimate | Mean [95% CI] | Simulator ground truth | Absolute gap |
| --- | ---: | ---: | ---: |
| IPS | 0.3917 [0.3741, 0.4094] | 0.3854 | 0.0063 |
| DR | 0.3900 [0.3827, 0.3972] | 0.3854 | 0.0046 |
| Logged behavior value | -0.1053 [-0.1077, -0.1029] | n/a | n/a |

## Fairness by synthetic user type

Values are mean bounded reward per decision, averaged over seeds. `FLAG` means the maximum group gap exceeds the configured descriptive threshold; it is a review signal, not a fairness certification.

| Policy | High-energy evening | Low-energy morning | Anxiety afternoon | Reflective evening | Max-min gap | Review |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `random` | -0.096 | -0.096 | -0.114 | -0.102 | 0.018 | OK |
| `epsilon_greedy` | 0.248 | 0.231 | 0.294 | 0.164 | 0.130 | OK |
| `linucb` | 0.314 | 0.250 | 0.303 | 0.209 | 0.106 | OK |
| `thompson_sampling` | 0.235 | 0.196 | 0.272 | 0.159 | 0.113 | OK |

## Five 'why this?' lines for wording review

1. Why this? Your recorded outcomes favor 4-7-8 style slow exhale when it matches your chosen activity preferences and your emotion labels include joy. This is an association, not a promise of benefit; you can choose another option or skip.
2. Why this? Your recorded outcomes favor one small action planner when it is morning and your emotion labels include sadness. This is an association, not a promise of benefit; you can choose another option or skip.
3. Why this? Your recorded outcomes favor 5-4-3-2-1 grounding when you have tried this activity recently and it is afternoon. This is an association, not a promise of benefit; you can choose another option or skip.
4. Why this? Your recorded outcomes favor name it to tame it: labelling when it matches your chosen activity preferences and your emotion labels include calm. This is an association, not a promise of benefit; you can choose another option or skip.
5. Why this? Your recorded outcomes favor a thought record when your energy rating is high and your emotion labels include joy. This is an association, not a promise of benefit; you can choose another option or skip.

## Limitations

- Outcomes and four user groups are synthetic and hand-specified; the simulator is not evidence of real-user benefit, safety, or clinical effectiveness.
- OPE is one-step contextual evaluation. Uniform synthetic behavior gives overlap, but real logs can be confounded, nonstationary, censored by overrides, and unsafe to explore.
- Thompson action propensities use finite Monte Carlo draws and are exact only for that finite-draw randomized implementation.
- The linear feature model cannot represent every interaction. The population prior is empirical-Bayes warm-starting, not a universal treatment effect.
- State and exploration ledgers are process-local. Durable per-user state, privacy review, native-language copy review, and deployment integration are prerequisites for product use.

MLflow run id: `f329789262f64a4ab624e0e72dd1ca42`.

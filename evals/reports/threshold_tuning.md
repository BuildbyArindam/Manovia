# Ensemble threshold tuning (Day 9)

- Date: 2026-10-10
- Split used: **dev only** (116 cases). The test split was never opened.
- Crisis cases in dev (HIGH+IMMINENT): 51
- Grid: min_confidence 0.3..0.7, crisis_mass_floor 0.3..0.6, suspicion_floor 0.1..0.4

**Chosen:** `safety_ml_min_confidence = 0.70`, `safety_ml_crisis_mass_floor = 0.30`, `safety_ml_suspicion_floor = 0.25`

Dev crisis recall at the chosen thresholds: **1.0000**; NONE/LOW cases handed a crisis card (HIGH+): **31**; NONE/LOW cases softened to a MEDIUM check-in: **7**.

Selection rule: maximise crisis recall; break ties by fewest crisis-card false alarms.

| min_confidence | crisis_mass_floor | suspicion_floor | dev crisis recall | crisis FAs | check-in FAs |
| --- | --- | --- | --- | --- | --- |
| 0.50 | 0.30 | 0.25 | 1.0000 | 31 | 7 |
| 0.55 | 0.30 | 0.25 | 1.0000 | 31 | 7 |
| 0.60 | 0.30 | 0.25 | 1.0000 | 31 | 7 |
| 0.65 | 0.30 | 0.25 | 1.0000 | 31 | 7 |
| 0.70 | 0.30 | 0.25 | 1.0000 | 31 | 7 | **(chosen)**
| 0.50 | 0.30 | 0.20 | 1.0000 | 31 | 16 |
| 0.55 | 0.30 | 0.20 | 1.0000 | 31 | 16 |
| 0.60 | 0.30 | 0.20 | 1.0000 | 31 | 16 |
| 0.65 | 0.30 | 0.20 | 1.0000 | 31 | 16 |
| 0.70 | 0.30 | 0.20 | 1.0000 | 31 | 16 |
| 0.50 | 0.30 | 0.10 | 1.0000 | 31 | 20 |
| 0.50 | 0.30 | 0.15 | 1.0000 | 31 | 20 |
| 0.55 | 0.30 | 0.10 | 1.0000 | 31 | 20 |
| 0.55 | 0.30 | 0.15 | 1.0000 | 31 | 20 |
| 0.60 | 0.30 | 0.10 | 1.0000 | 31 | 20 |

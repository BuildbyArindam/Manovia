"""Day 33: the safety classifier — cost-sensitive training, calibration, abstention.

One pipeline with the entry points the brief asks for:

| module | what it does |
| --- | --- |
| `taxonomy` | the five levels, the categories, and the **cost matrix** every other module reads |
| `cost` | the cost-weighted cross-entropy, the expected-cost decision rule,
    and the cost table in text form |
| `providers` | the paraphraser interface: `FakeParaphraser` (offline,
    deterministic) and a bridge to the backend's LLM provider chain |
| `augment` | template + paraphrase expansion to 2,000+ synthetic rows with provenance,
    plus the adversarial augmentations (typos, spacing, romanisation, code-mixing, emoji) |
| `data` | loading, the rules-engine label-consistency filter, de-duplication, split assignment,
    the overlap check against the frozen test split |
| `model` | the LoRA builder for a single-label five-class head |
| `calibration` | temperature scaling, ECE and the reliability-diagram data |
| `conformal` | split conformal prediction sets with a target coverage,
    and the abstain -> MEDIUM policy |
| `explain` | integrated-gradients token attributions and the shortcut-learning review script |
| `baselines` | the Day-9 TF-IDF baseline, retrained here so both numbers come from the same data |
| `train` | the run: cost-sensitive loss, calibration, conformal, MLflow, artefact, report, card |
| `evaluate` | inference only — it refuses to run in a process that imported the trainer |
| `report` | the report and the model card writers |

Nothing here is wired into the API's safety path except through the reviewed
ensemble interface, and nothing here can lower a level the rules engine found.
"""

from __future__ import annotations

from manovia_ml.training.safety.taxonomy import (
    COST_MATRIX,
    LEVELS,
    class_weights_from_cost,
    expected_cost_level,
    level_index,
)

__all__ = [
    "COST_MATRIX",
    "LEVELS",
    "class_weights_from_cost",
    "expected_cost_level",
    "level_index",
]

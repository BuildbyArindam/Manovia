"""Command-line entry point for the reproducible Day-36 synthetic benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from manovia_ml.mood_forecast.config import load_mood_forecast_config
from manovia_ml.mood_forecast.experiment import run_mood_forecast_experiment
from manovia_ml.paths import config_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="mood_forecast.yaml",
        help="YAML filename in ml/configs/ or an explicit path",
    )
    parser.add_argument(
        "--allow-baselines-only",
        action="store_true",
        help="when PyTorch is absent, explicitly permit a baseline-only run and report",
    )
    args = parser.parse_args(argv)
    candidate = Path(args.config)
    if not candidate.is_absolute() and not candidate.is_file():
        candidate = config_dir() / candidate
    try:
        config = load_mood_forecast_config(candidate)
        summary = run_mood_forecast_experiment(
            config,
            config_path=candidate.resolve(),
            allow_baselines_only=args.allow_baselines_only,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"mood forecast run failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

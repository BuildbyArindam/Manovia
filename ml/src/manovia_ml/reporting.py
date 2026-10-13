"""Reports: turn a run id into a Markdown page a human can read.

Reports are generated, reviewed and then committed (``ml/reports/``) — they are
the only part of a run that belongs in Git, because they hold numbers and
hashes rather than data or weights. The template is deliberately a *template*:
every report carries the commit, the config fingerprint, the data hashes, the
seed and the metric table, so a claim in a PR can be checked against the run
that produced it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from manovia_ml.mlflow_api import MlflowClient, mlflow
from manovia_ml.paths import default_tracking_uri

__all__ = ["main", "render_report", "write_run_report"]

_REPORT_TEMPLATE = """# {title}

Generated from MLflow run `{run_id}` on {generated} by
`python -m manovia_ml.reporting --run {run_id}`. Do not edit by hand: re-generate
it from the run.

## What ran

| field | value |
| --- | --- |
| config | `{config}` |
| config sha256 | `{config_sha256}` |
| phase | `{phase}` |
| git commit | `{git_commit}` (branch `{git_branch}`, dirty `{git_dirty}`) |
| seed | `{seed}` |
| python | `{python_version}` |
| platform | `{platform}` |

## Data

| split | path | sha256 | rows |
| --- | --- | --- | --- |
{data_rows}

## Metrics

| metric | value |
| --- | --- |
{metric_rows}

{metric_context}

## Environment (libraries that can move a number)

{library_rows}

{extra}## Reproduce

```bash
make ml-train CONFIG={config_path}{record_hint}
make ml-eval RUN={run_id}
```

The baseline for this config lives in `ml/configs/expected/{config_file}.json`;
a later run with the same seed must match it within the recorded tolerance.
"""


def _fmt(value: Any, default: str = "—") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _table(rows: Sequence[tuple[str, ...]]) -> str:
    """A Markdown table from tuples; an empty sequence renders a placeholder row."""

    if not rows:
        return "| — | — |"
    return "\n".join("| " + " | ".join(_escape(cell) for cell in row) + " |" for row in rows)


def _escape(value: str) -> str:
    return value.replace("|", "\\|")


def render_report(
    *,
    run_id: str,
    params: dict[str, str],
    metrics: dict[str, float],
    tags: dict[str, str],
    data: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
    title: str | None = None,
    config_path: str | None = None,
    extra: str = "",
) -> str:
    config_name = params.get("configname") or tags.get("manovia.config") or "run"
    data_rows = []
    for dataset in (data or {}).get("datasets", []):
        data_rows.append(
            (
                _fmt(dataset.get("name")),
                _fmt(dataset.get("path")),
                _fmt(dataset.get("sha256")),
                _fmt(dataset.get("rows")),
            )
        )
    libraries = (environment or {}).get("libraries") or {}
    library_rows = "\n".join(
        f"- `{name}` {version}" for name, version in sorted(libraries.items()) if name
    )
    config_file = Path(config_path or config_name).name.removesuffix(".yaml")
    return _REPORT_TEMPLATE.format(
        title=title or f"{config_name} — {tags.get('manovia.phase', 'run')}",
        run_id=run_id,
        generated=datetime.now(UTC).date().isoformat(),
        config=config_name,
        config_sha256=_fmt(params.get("config.sha256")),
        phase=_fmt(tags.get("manovia.phase")),
        git_commit=_fmt(params.get("git.commit")),
        git_branch=_fmt(params.get("git.branch")),
        git_dirty=_fmt(params.get("git.dirty")),
        seed=_fmt(params.get("seed")),
        python_version=_fmt(params.get("python.version")),
        platform=_fmt(params.get("platform")),
        data_rows=_table(data_rows),
        metric_rows=_table([(name, f"{value:.6f}") for name, value in sorted(metrics.items())]),
        metric_context=_metric_context(metrics),
        library_rows=library_rows or "- none recorded",
        extra=extra,
        config_path=config_path or f"configs/{config_file}.yaml",
        config_file=config_file,
        record_hint="  # add --record-expected to reset the baseline deliberately",
    )


def _metric_context(metrics: dict[str, float]) -> str:
    """One line that stops small-split accuracy from being read as quality.

    Two numbers need a sentence next to them: `majority_accuracy` (what a
    constant classifier scores on this eval set — the floor) and
    `train_accuracy` (whether the pipeline can still fit its own training rows).
    Both are logged on every run precisely so a report cannot be quoted without
    them.
    """

    notes: list[str] = []
    accuracy = metrics.get("accuracy")
    majority = metrics.get("majority_accuracy")
    train_accuracy = metrics.get("train_accuracy")
    if accuracy is not None and majority is not None:
        delta = accuracy - majority
        verdict = (
            "above the floor"
            if delta > 0
            else "at or below the floor — a constant classifier would do as well"
        )
        notes.append(
            f"**accuracy vs the floor:** {accuracy:.6f} accuracy against a "
            f"majority-class rate of {majority:.6f} on this eval split "
            f"({delta:+.6f}, {verdict})."
        )
    if train_accuracy is not None:
        notes.append(
            f"**in-sample accuracy:** {train_accuracy:.6f} on the training rows — this, "
            "not held-out accuracy, is the canary for a broken pipeline on a tiny fixture."
        )
    return "\n\n".join(notes)


def _load_json_artifact(run_id: str, name: str, destination: Path) -> dict[str, Any] | None:
    try:
        path = mlflow.artifacts.download_artifacts(
            run_id=run_id, artifact_path=name, dst_path=str(destination)
        )
    except Exception:
        return None
    candidate = Path(str(path))
    if candidate.is_dir():
        candidate = candidate / name
    if not candidate.is_file():
        return None
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def write_run_report(
    run_id: str,
    out_path: Path | str,
    *,
    tracking_uri: str | None = None,
    title: str | None = None,
    config_path: str | None = None,
    extra: str = "",
) -> Path:
    mlflow.set_tracking_uri(tracking_uri or default_tracking_uri())
    client = MlflowClient()
    run = client.get_run(run_id)
    params = {name: str(value) for name, value in run.data.params.items()}
    metrics = {name: float(value) for name, value in run.data.metrics.items()}
    tags = {name: str(value) for name, value in run.data.tags.items()}

    with tempfile.TemporaryDirectory(prefix="manovia-ml-report-") as tmp:
        tmp_path = Path(tmp)
        data = _load_json_artifact(run_id, "data.json", tmp_path)
        environment = _load_json_artifact(run_id, "environment.json", tmp_path)
        expected = _load_json_artifact(run_id, "expected.json", tmp_path)

    if expected:
        extra = (
            f"## Baseline check\n\n"
            f"Checked against `ml/configs/expected/`: "
            f"{'within tolerance' if not expected.get('problems') else '**DRIFTED**'}\n\n"
            f"```json\n{json.dumps(expected, indent=2, sort_keys=True)}\n```\n\n"
        ) + extra

    report = render_report(
        run_id=run_id,
        params=params,
        metrics=metrics,
        tags=tags,
        data=data,
        environment=environment,
        title=title,
        config_path=config_path,
        extra=extra,
    )
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report, encoding="utf-8")
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.reporting",
        description="Write a Markdown report for a logged run (committed under ml/reports/).",
    )
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", default=None, help="default: reports/<run id>.md")
    parser.add_argument("--tracking-uri", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--config-path", default=None)
    args = parser.parse_args(argv)

    from manovia_ml.paths import reports_dir

    out = Path(args.out) if args.out else reports_dir() / f"{args.run}.md"
    written = write_run_report(
        args.run,
        out,
        tracking_uri=args.tracking_uri,
        title=args.title,
        config_path=args.config_path,
    )
    print(f"REPORT={written}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())

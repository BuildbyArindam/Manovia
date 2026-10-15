"""Reproducible report, attack and main SVG figure. Never persists models or rows."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yaml
from sklearn.metrics import roc_auc_score

from manovia_ml.federated.config import FederatedConfig
from manovia_ml.federated.data import Cohort, cohort
from manovia_ml.federated.training import federated, fit, model, private
from manovia_ml.hashing import DatasetRef
from manovia_ml.seeds import seed_everything
from manovia_ml.tracking import tracked_run


def losses(net: Any, data: Any) -> Any:
    with torch.no_grad():
        return (net(data.tensors[0]) - data.tensors[1]).square().flatten().numpy()


def attack_auc(net: Any, data: Cohort) -> float:
    """Known true target; continuous negative-loss score, no test-tuned threshold.

    Equal member/nonmember counts sampled deterministically within each user
    mixture. No query text, no identities; AUC is descriptive, not a DP proof.
    """
    member = losses(net, data.train)
    nonmember = losses(net, data.test)
    rng = np.random.default_rng(0)
    n = min(len(member), len(nonmember))
    member = rng.choice(member, n, replace=False)
    nonmember = rng.choice(nonmember, n, replace=False)
    return float(roc_auc_score([1] * n + [0] * n, np.r_[-member, -nonmember]))


def mase(net: Any, data: Cohort) -> float:
    with torch.no_grad():
        return float((net(data.test.tensors[0]) - data.test.tensors[1]).abs().mean()) / data.scale


def run(config: FederatedConfig) -> tuple[list[dict[str, Any]], str]:
    seed_state = seed_everything(config.seed)
    torch.set_num_threads(1)
    data = cohort(config, iid=False)
    if config.delta >= 1 / len(data.train):
        raise ValueError("delta must be smaller than inverse training record count")
    ref = DatasetRef(
        "day38-synthetic-mood-pairs",
        "generated:state-space-v1",
        data.sha256,
        len(data.train) + len(data.test),
    )
    rows: list[dict[str, Any]] = []
    with tracked_run(config, phase=config.phase, data_refs=[ref], seed_state=seed_state) as tracked:

        def record(
            name: str,
            net: Any,
            curve: list[float],
            *,
            epsilon: float | None = None,
            noise: float | None = None,
            clip: float | None = None,
            steps: int = 0,
            participants: list[int] | None = None,
        ) -> None:
            row: dict[str, Any] = {
                "name": name,
                "mase": mase(net, data),
                "auc": attack_auc(net, data),
                "epsilon": epsilon,
                "noise": noise,
                "clip": clip,
                "delta": config.delta if epsilon is not None else None,
                "curve": curve,
                "steps": steps,
                "participants": participants,
            }
            rows.append(row)
            index = len(rows)
            tracked.log_metrics(
                {f"model_{index}.mase": row["mase"], f"model_{index}.attack_auc": row["auc"]}
            )
            for step, value in enumerate(curve):
                tracked.log_metrics({f"model_{index}.convergence_mase": value}, step=step)

        central = model(config)
        curve = []
        # Same number of examples processed as the no-dropout federation;
        # central takes local_steps full-cohort gradients per round.
        for _ in range(config.rounds):
            fit(central, data.train, config, steps=config.local_steps)
            curve.append(mase(central, data))
        record("centralised", central, curve, steps=config.rounds * config.local_steps)
        for iid in (True, False):
            clients = cohort(config, iid=iid).clients
            for dropout in config.dropout:
                for algorithm, mu in (("FedAvg", 0.0), ("FedProx", config.proximal_mu)):
                    net, curve, participants = federated(
                        clients, config, mu=mu, dropout=dropout, evaluate=lambda n: mase(n, data)
                    )
                    record(
                        f"{algorithm} {'IID' if iid else 'non-IID'} drop={dropout}",
                        net,
                        curve,
                        participants=participants,
                    )
        for noise in config.noise:
            for clip in config.clipping:
                net, epsilon, curve, steps = private(
                    data.train, config, noise=noise, clip=clip, evaluate=lambda n: mase(n, data)
                )
                record(
                    f"DP sigma={noise} C={clip}",
                    net,
                    curve,
                    epsilon=epsilon,
                    noise=noise,
                    clip=clip,
                    steps=steps,
                )
        tracked.log_dict(
            {"rows": rows, "client_target_std": data.client_target_std}, "aggregate-results.json"
        )
        report = render(config, rows, tracked.run_id, data)
        output = Path(config.report_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        figure = output.with_suffix(".svg")
        figure.write_text(svg(rows), encoding="utf-8")
        tracked.log_artifact(output)
        tracked.log_artifact(figure)
        return rows, tracked.run_id


def render(config: FederatedConfig, rows: list[dict[str, Any]], run_id: str, data: Cohort) -> str:
    persistence = float((data.test.tensors[1] - data.test.tensors[0][:, :1]).abs().mean())
    lines = [
        f"# Day 38 — synthetic {config.phase} evidence (not real-user training)",
        "",
        f"MLflow run: `{run_id}`. Seed: `{config.seed}`. Clients: {config.clients}.",
        f"Data SHA256: `{data.sha256}`; train/test pairs: {len(data.train)}/{len(data.test)}.",
        f"Client mean-target SD (normalised): {data.client_target_std:.4f}.",
        f"Persistence held-out MASE: "
        f"{persistence / data.scale:.4f}; "
        f"training persistence denominator (normalised mood): {data.scale:.4f}.",
        "",
        f"![Convergence]({Path(config.report_output).with_suffix('.svg').name})",
        "",
        "| Model | MASE ↓ | attack AUC | epsilon | delta | optimizer steps |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        budget = "not DP" if row["epsilon"] is None else f"{row['epsilon']:.4f}"
        delta = "—" if row["delta"] is None else str(row["delta"])
        lines.append(
            f"| {row['name']} | {row['mase']:.4f} | {row['auc']:.4f} | "
            f"{budget} | {delta} | {row['steps'] or 'local, see config'} |"
        )
    chosen = next(
        (
            r
            for r in rows
            if r["noise"] == config.operating_noise and r["clip"] == config.operating_clip
        ),
        None,
    )
    if chosen is None:
        raise ValueError("predeclared operating point must be present in the sweep")
    private_rows = [r for r in rows if r["epsilon"] is not None]
    composition = sum(r["epsilon"] for r in private_rows)
    lines.extend(
        [
            "",
            "## Operating point (research only)",
            "",
            f"Predeclared research operating point: **{chosen['name']}**, epsilon "
            f"{chosen['epsilon']:.4f} at delta {config.delta}. "
            "Highest configured noise reduces epsilon; C=1 allows more signal than C=0.5. "
            "No holdout-based selection, no deployment recommendation. MASE difference from "
            f"central MASE: {chosen['mase'] - rows[0]['mase']:+.4f}.",
            f"Attack AUC: non-private {rows[0]['auc']:.4f}, chosen DP "
            f"{chosen['auc']:.4f}. Distance to chance changes from "
            f"{abs(rows[0]['auc'] - 0.5):.4f} to {abs(chosen['auc'] - 0.5):.4f}.",
            "",
            f"If all {len(private_rows)} DP models were released on real private data, "
            f"basic composition gives epsilon ≤ {composition:.4f}, delta ≤ "
            f"{len(private_rows) * config.delta:g}, before selection/metric releases.",
            "",
            "## Limits and interpretation",
            "",
            "- All inputs are MIT-authored synthetic Day-36 state-space data; "
            "catalogue entry: Manovia synthetic federated mood pairs (Day 38).",
            "- A small next-day regression head, not the Day-36 GRU or a clinical model. "
            "MASE uses mean |next-day minus current-day| on training pairs. "
            "Fixed public normalisation. No categorical accuracy is applicable.",
            "- Nonoverlapping pairs, random within-person holdout, same people at test; "
            "not unseen-user or prospective performance. One seed; no confidence claim.",
            "- Flower FedAvg/FedProx aggregate_fit runs serially in-process; no Ray, "
            "device transport, secure aggregation, or DP federated updates. "
            "Dropout is independent per round, not adversarial or mid-upload.",
            "- DP is central record-level add/remove-one-pair Opacus RDP, not user-level. "
            "Full-cohort central/FL and Poisson minibatch DP have different optimizer "
            "step counts. No fair compute/utility superiority claim.",
            "- Seeded noncryptographic RNG, secure_mode=False: synthetic research only. "
            "Every DP sweep is a separate hypothetical release. Releasing all requires "
            "composition; test metrics and private-data selection also require accounting.",
            "- Loss-score AUC may already be near chance, or move away from chance. "
            "A single tiny attack does NOT demonstrate a defence or prove privacy. "
            "Do not change attack direction or tune on holdout to manufacture success.",
            "",
            "## Convergence and participation",
            "",
            "Values below are held-out MASE after each round/DP epoch; not tuning input.",
        ]
    )
    for row in rows:
        lines.append(f"- {row['name']}: " + ", ".join(f"{v:.4f}" for v in row["curve"]))
        if row["participants"] is not None:
            lines.append(f"  Responding clients: {row['participants']}.")
    return "\n".join(lines) + "\n"


def svg(rows: list[dict[str, Any]]) -> str:
    """Standalone vector figure; no plotting dependency or embedded dataset."""
    values = [v for r in rows for v in r["curve"]]
    low, high = min(values), max(values)
    colors = ["#111111", "#005ab5", "#a53b00", "#14733c", "#8c32a8"]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="700" '
        'role="img" aria-label="Synthetic held-out MASE convergence by round">',
        '<rect width="1100" height="700" fill="white"/>',
        '<text x="30" y="25">Day 38 synthetic experiment: MASE (lower is better)</text>',
        '<path d="M60 45 V480 H610" fill="none" stroke="black"/>',
    ]
    for i, row in enumerate(rows):
        color = colors[i % len(colors)]
        points = " ".join(
            f"{60 + j * 540 / max(1, len(row['curve']) - 1):.1f},"
            f"{470 - (v - low) / max(high - low, 1e-8) * 400:.1f}"
            for j, v in enumerate(row["curve"])
        )
        parts.append(
            f'<polyline points="{points}" stroke="{color}" fill="none" '
            f'stroke-dasharray="{i // len(colors) + 1} 2"/>'
        )
        parts.append(
            f'<text x="630" y="{50 + i * 24}" fill="{color}" '
            f'font-size="13">{row["name"]}: {row["mase"]:.3f}</text>'
        )
    parts.extend(
        [
            f'<text x="10" y="60">{high:.3f}</text>',
            f'<text x="10" y="480">{low:.3f}</text>',
            '<text x="70" y="510">Round / DP epoch (different optimizer budgets)</text>',
            "</svg>",
        ]
    )
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/federated_smoke.yaml")
    parser.add_argument("--seed", type=int, help="Explicit independent repeat; logged in config")
    parser.add_argument("--report-output", help="Separate report path for each repeat")
    args = parser.parse_args()
    raw = yaml.safe_load(Path(args.config).read_text())
    if args.seed is not None:
        raw["seed"] = args.seed
    if args.report_output is not None:
        raw["report_output"] = args.report_output
    config = FederatedConfig.model_validate(raw)
    _, run_id = run(config)
    print(Path(config.report_output).read_text())
    print(f"MLflow run id: {run_id}")


if __name__ == "__main__":
    main()

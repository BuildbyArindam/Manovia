"""Flower/Opacus contracts, using real libraries and synthetic-only inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("torch")
pytest.importorskip("flwr")
pytest.importorskip("opacus")
import torch
from opacus import PrivacyEngine
from opacus.accountants import RDPAccountant
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from manovia_ml.config import TrackingConfig
from manovia_ml.federated.config import FederatedConfig
from manovia_ml.federated.data import cohort
from manovia_ml.federated.experiment import attack_auc, run
from manovia_ml.federated.training import aggregate, federated, fit, load, model, parameters


def test_flower_weighted_aggregation_and_fedprox() -> None:
    updates = [
        (1, [np.array([1.0, 3.0]), np.array([2.0])]),
        (3, [np.array([5.0, 7.0]), np.array([6.0])]),
    ]
    for mu in (0.0, 0.1):
        result = aggregate(updates, proximal_mu=mu)
        np.testing.assert_allclose(result[0], [4, 6])
        np.testing.assert_allclose(result[1], [5])
    # Aggregation must not mutate a peer's input.
    np.testing.assert_array_equal(updates[0][1][0], [1, 3])


@pytest.mark.parametrize(
    "updates",
    [
        [],
        [(0, [np.ones(1)])],
        [(1, [np.ones(1)]), (1, [np.ones(2)])],
        [(1, [np.array([float("nan")])])],
    ],
)
def test_aggregation_rejects_invalid_updates(updates: Any) -> None:
    with pytest.raises(ValueError):
        aggregate(updates)


@pytest.mark.parametrize("clip", [0.05, 0.5, 2.0])
def test_real_opacus_clips_joint_per_example_norm(clip: float) -> None:
    """Compare actual Opacus accumulated gradients to independent clipped sum.

    Tests a joint norm over weight AND bias, not separate per-layer clipping,
    and deliberately includes one huge outlier. Noise is zero only in this
    clipping unit test; no privacy budget is claimed for it.
    """
    net = nn.Linear(2, 1)
    with torch.no_grad():
        net.weight.fill_(1)
        net.bias.fill_(1)
    x = torch.tensor([[100.0, -50.0], [1.0, 2.0], [0.0, 0.0]])
    y = torch.zeros(3, 1)
    loader = DataLoader(TensorDataset(x, y), batch_size=3)
    optimizer = torch.optim.SGD(net.parameters(), lr=0.1)
    net, optimizer, _ = PrivacyEngine().make_private(
        module=net,
        optimizer=optimizer,
        data_loader=loader,
        noise_multiplier=0,
        max_grad_norm=clip,
        poisson_sampling=False,
    )
    nn.functional.mse_loss(net(x), y).backward()
    grads = [p.grad_sample.reshape(3, -1) for p in net.parameters()]
    joined = torch.cat(grads, dim=1)
    norms = joined.norm(2, dim=1)
    factors = (clip / (norms + 1e-6)).clamp(max=1)
    expected = joined * factors[:, None]
    assert torch.all(expected.norm(2, dim=1) <= clip + 1e-6)
    assert norms.max() > 10 * clip
    optimizer.clip_and_accumulate()
    actual = torch.cat([p.summed_grad.flatten() for p in net.parameters()])
    torch.testing.assert_close(actual, expected.sum(dim=0))
    assert float(actual.norm()) <= 3 * clip + 1e-5


def epsilon(*, noise: float = 1.5, q: float = 0.1, steps: int = 10, delta: float = 1e-5) -> float:
    accountant = RDPAccountant()
    for _ in range(steps):
        accountant.step(noise_multiplier=noise, sample_rate=q)
    return float(accountant.get_epsilon(delta=delta))


def test_accountant_monotonicity() -> None:
    baseline = epsilon()
    assert epsilon(steps=20) > baseline
    assert epsilon(noise=3) < baseline
    assert epsilon(q=0.2) > baseline
    assert epsilon(delta=1e-6) > baseline


def test_fedprox_penalty_reduces_local_drift() -> None:
    config = FederatedConfig(clients=3, days=35, learning_rate=0.05)
    data = cohort(config, iid=False).clients[0]
    avg, prox = model(config), model(config)
    initial = parameters(avg)
    fit(avg, data, config, steps=10, reference=initial, mu=0)
    fit(prox, data, config, steps=10, reference=initial, mu=2)

    def drift(net: Any) -> float:
        return float(
            sum(np.square(p - w).sum() for p, w in zip(parameters(net), initial, strict=True))
        )

    assert 0 < drift(prox) < drift(avg)


def test_dropout_all_clients_keeps_previous_model(monkeypatch: pytest.MonkeyPatch) -> None:
    class DropAll:
        def random(self) -> float:
            return 0.0

    config = FederatedConfig(clients=3, rounds=2)
    clients = cohort(config, iid=False).clients
    monkeypatch.setattr(np.random, "default_rng", lambda _seed: DropAll())
    result, curve, participants = federated(
        clients, config, mu=0, dropout=0.5, evaluate=lambda _net: 1.0
    )
    assert participants == [0, 0]
    assert curve == [1.0, 1.0]
    for actual, expected in zip(parameters(result), parameters(model(config)), strict=True):
        np.testing.assert_array_equal(actual, expected)


def test_missing_observations_are_skipped_and_iid_preserves_rows() -> None:
    # Regression: the Day-36 generator jitters per-user logging below 1 even
    # when the cohort's base probability is 1; asserting every day exists fails.
    config = FederatedConfig()
    noniid, iid = cohort(config, iid=False), cohort(config, iid=True)
    assert len(noniid.train) + len(noniid.test) < config.clients * (config.days // 2)
    assert len(noniid.clients) == 50
    assert all(len(c) > 0 for c in noniid.clients)
    assert noniid.sha256 == iid.sha256
    for a, b in zip(noniid.test.tensors, iid.test.tensors, strict=True):
        torch.testing.assert_close(a, b)
    original = sorted(tuple(row) for row in torch.cat(noniid.train.tensors, dim=1).tolist())
    shuffled = sorted(
        tuple(row) for c in iid.clients for row in torch.cat(c.tensors, dim=1).tolist()
    )
    assert original == shuffled


def test_attack_uses_negative_loss_direction() -> None:
    config = FederatedConfig(clients=3)
    data = cohort(config, iid=False)
    net = model(config)
    # A model with identical errors on all rows has chance AUC, not a fabricated
    # "defence success". Replace labels so its train and test losses are zero.
    with torch.no_grad():
        data.train = TensorDataset(data.train.tensors[0], net(data.train.tensors[0]))
        data.test = TensorDataset(data.test.tensors[0], net(data.test.tensors[0]))
    assert attack_auc(net, data) == 0.5


@pytest.mark.parametrize(
    "field,value",
    [("noise", [0]), ("clipping", [-1]), ("dropout", [1.0]), ("noise", []), ("unexpected_knob", 7)],
)
def test_strict_config_rejects_invalid_sweeps(field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        FederatedConfig.model_validate({field: value})


def test_end_to_end_and_seed_reproducibility(tmp_path: Path) -> None:
    config = FederatedConfig(
        clients=3,
        days=35,
        rounds=1,
        local_steps=1,
        noise=(2.0,),
        clipping=(0.5,),
        dropout=(0.0,),
        batch_size=16,
        report_output=str(tmp_path / "day38.md"),
        tracking=TrackingConfig(experiment="test-day38", tracking_uri=(tmp_path / "runs").as_uri()),
    )
    rows, run_id = run(config)
    repeated, other_id = run(config)
    assert run_id != other_id
    assert rows == repeated
    assert len(rows) == 6
    assert rows[-1]["epsilon"] > 0
    assert rows[-1]["steps"] > 0
    assert all(0 <= r["auc"] <= 1 for r in rows)
    assert "not user-level" in Path(config.report_output).read_text()
    assert Path(config.report_output).with_suffix(".svg").exists()
    # With exactly one local step and no dropout, weighted FedAvg equals a
    # full-cohort central gradient (including unequal client sizes).
    assert abs(rows[0]["mase"] - rows[1]["mase"]) < config.tolerance
    assert abs(rows[0]["mase"] - rows[3]["mase"]) < config.tolerance


def test_parameter_load_roundtrip() -> None:
    config = FederatedConfig()
    first, second = model(config), model(config)
    with torch.no_grad():
        for p in first.parameters():
            p.add_(1)
    load(second, parameters(first))
    for a, b in zip(parameters(first), parameters(second), strict=True):
        np.testing.assert_array_equal(a, b)

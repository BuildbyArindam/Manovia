"""Real Flower strategy aggregation and Opacus DP-SGD, without Ray or RPC."""

from __future__ import annotations

from typing import Any

import numpy as np
import torch
from flwr.common import Code, FitRes, Status, ndarrays_to_parameters, parameters_to_ndarrays
from flwr.server.strategy import FedAvg, FedProx
from opacus import PrivacyEngine
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from manovia_ml.federated.config import FederatedConfig
from manovia_ml.seeds import derive_seed


def model(config: FederatedConfig) -> Any:
    torch.manual_seed(config.seed)
    return nn.Sequential(nn.Linear(6, config.hidden), nn.Tanh(), nn.Linear(config.hidden, 1))


def parameters(net: Any) -> list[Any]:
    return [p.detach().numpy().copy() for p in net.parameters()]


def load(net: Any, weights: list[Any]) -> None:
    with torch.no_grad():
        for p, w in zip(net.parameters(), weights, strict=True):
            p.copy_(torch.tensor(w))


def fit(
    net: Any,
    data: TensorDataset,
    config: FederatedConfig,
    *,
    steps: int,
    reference: list[Any] | None = None,
    mu: float = 0.0,
) -> None:
    optimizer = torch.optim.SGD(net.parameters(), lr=config.learning_rate)
    x, y = data.tensors
    for _ in range(steps):
        optimizer.zero_grad()
        loss = nn.functional.mse_loss(net(x), y)
        if reference is not None:
            loss = loss + mu / 2 * sum(
                (p - torch.tensor(w)).square().sum()
                for p, w in zip(net.parameters(), reference, strict=True)
            )
        loss.backward()
        optimizer.step()


def aggregate(updates: list[tuple[int, list[Any]]], *, proximal_mu: float = 0.0) -> list[Any]:
    """Use Flower's actual weighted aggregator, rejecting invalid toy/peer inputs."""
    if not updates or any(n <= 0 for n, _ in updates):
        raise ValueError("aggregation requires positive sample counts")
    expected = [w.shape for w in updates[0][1]]
    for _, weights in updates:
        if [w.shape for w in weights] != expected or any(not np.isfinite(w).all() for w in weights):
            raise ValueError("incompatible or non-finite update")
    strategy = (
        FedProx(proximal_mu=proximal_mu, min_fit_clients=1, min_available_clients=1)
        if proximal_mu
        else FedAvg(min_fit_clients=1, min_available_clients=1)
    )
    results = [
        (None, FitRes(Status(Code.OK, "ok"), ndarrays_to_parameters(w), n, {})) for n, w in updates
    ]
    result, _ = strategy.aggregate_fit(1, results, [])
    if result is None:
        raise RuntimeError("Flower returned no aggregate")
    return list(parameters_to_ndarrays(result))


def federated(
    clients: list[TensorDataset],
    config: FederatedConfig,
    *,
    mu: float,
    dropout: float,
    evaluate: Any,
) -> tuple[Any, list[float], list[int]]:
    net = model(config)
    rng = np.random.default_rng(config.seed)
    curve, participants = [], []
    for _ in range(config.rounds):
        weights = parameters(net)
        selected = [i for i in range(len(clients)) if rng.random() >= dropout]
        if not selected:
            # No update, no fabricated participant. Carry the previous model forward.
            curve.append(float(evaluate(net)))
            participants.append(0)
            continue
        updates = []
        for i in selected:
            local = model(config)
            load(local, weights)
            fit(local, clients[i], config, steps=config.local_steps, reference=weights, mu=mu)
            updates.append((len(clients[i]), parameters(local)))
        load(net, aggregate(updates, proximal_mu=mu))
        curve.append(float(evaluate(net)))
        participants.append(len(selected))
    return net, curve, participants


def private(
    data: TensorDataset, config: FederatedConfig, *, noise: float, clip: float, evaluate: Any
) -> tuple[Any, float, list[float], int]:
    """Central trusted-trainer, add/remove one-pair adjacency; NOT user-level FL DP.

    secure_mode=False is explicitly smoke/research only. A production release
    requires a cryptographic RNG and independent privacy review. Poisson sampling
    and the RDP hook are Opacus-owned, including empty batches and actual steps.
    """
    net = model(config)
    # Distinct reproducible streams per release; never reuse Gaussian noise
    # across sweep arms. Public seeded research is still not production DP.
    torch.manual_seed(derive_seed(config.seed, "dp", str(noise), str(clip)))
    optimizer = torch.optim.SGD(net.parameters(), lr=config.learning_rate)
    loader = DataLoader(data, batch_size=min(config.batch_size, len(data)))
    engine = PrivacyEngine(accountant="rdp", secure_mode=False)
    net, optimizer, loader = engine.make_private(
        module=net,
        optimizer=optimizer,
        data_loader=loader,
        noise_multiplier=noise,
        max_grad_norm=clip,
        poisson_sampling=True,
    )
    curve = []
    steps = 0
    for _ in range(config.rounds):
        for x, y in loader:
            optimizer.zero_grad()
            loss = nn.functional.mse_loss(net(x), y)
            loss.backward()
            optimizer.step()
            steps += 1
        curve.append(float(evaluate(net)))
    return net, float(engine.get_epsilon(config.delta)), curve, steps

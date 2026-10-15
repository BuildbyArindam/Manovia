"""Disjoint next-day pairs from the Day-36 synthetic state-space generator."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import torch
from torch.utils.data import TensorDataset

from manovia_ml.federated.config import FederatedConfig
from manovia_ml.mood_forecast.synthetic import generate_synthetic_panel


@dataclass
class Cohort:
    clients: list[TensorDataset]
    train: TensorDataset
    test: TensorDataset
    sha256: str
    scale: float
    train_pair_ids: tuple[str, ...]
    test_pair_ids: tuple[str, ...]
    client_target_std: float


def cohort(config: FederatedConfig, *, iid: bool) -> Cohort:
    """No fitted preprocessing. Pair endpoints never cross membership splits.

    IID control shuffles *the same training rows* between clients; evaluation is
    unchanged. Non-IID clients each hold one synthetic person's history. The
    random within-person split is not a prospective forecasting evaluation.
    """
    panel = generate_synthetic_panel(
        seed=config.seed, users=config.clients, days=config.days, logging_probability=1.0
    )
    rng = np.random.default_rng(config.seed)
    clients = []
    tests = []
    scales = []
    train_ids: list[str] = []
    test_ids: list[str] = []
    for user in panel.users:
        features, targets, ids = [], [], []
        for a, b in zip(user.days[::2], user.days[1::2], strict=False):
            if a.valence is None or b.valence is None or a.energy is None:
                continue
            features.append(
                [
                    (a.valence - 3) / 2,
                    (a.energy - 3) / 2,
                    float(bool(a.sleep_tag)),
                    float(bool(a.exercise_done)),
                    np.sin(2 * np.pi * a.day.weekday() / 7),
                    np.cos(2 * np.pi * a.day.weekday() / 7),
                ]
            )
            targets.append((b.valence - 3) / 2)
            ids.append(f"{user.user_id}:{a.day_index}-{b.day_index}")
        if len(targets) < 3:
            raise ValueError("too few observed nonoverlapping pairs for a client")
        indices = rng.permutation(len(targets))
        n_test = max(1, int(len(indices) * config.test_fraction))
        x = torch.tensor(features, dtype=torch.float32)
        y = torch.tensor(targets, dtype=torch.float32).reshape(-1, 1)
        tr, te = indices[n_test:], indices[:n_test]
        train_ids.extend(ids[i] for i in tr)
        test_ids.extend(ids[i] for i in te)
        clients.append(TensorDataset(x[tr], y[tr]))
        tests.append(TensorDataset(x[te], y[te]))
        scales.extend(abs(y[tr] - x[tr, :1]).flatten().tolist())
    train = TensorDataset(*(torch.cat([c.tensors[i] for c in clients]) for i in range(2)))
    test = TensorDataset(*(torch.cat([c.tensors[i] for c in tests]) for i in range(2)))
    digest = hashlib.sha256()
    for tensor in (*train.tensors, *test.tensors):
        digest.update(tensor.numpy().tobytes())
    spread = float(np.std([c.tensors[1].mean().item() for c in clients]))
    if iid:
        order = torch.tensor(rng.permutation(len(train)))
        clients = [
            TensorDataset(*(t[part] for t in train.tensors))
            for part in torch.tensor_split(order, config.clients)
        ]
    return Cohort(
        clients,
        train,
        test,
        digest.hexdigest(),
        max(float(np.mean(scales)), 1e-8),
        tuple(train_ids),
        tuple(test_ids),
        spread,
    )

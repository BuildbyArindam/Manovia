"""Lazy-imported GRU, LSTM and small Transformer sequence encoders.

Importing this module never imports PyTorch. The research benchmark can run all
four baselines and report its limitations in a CPU-only environment.
"""

from __future__ import annotations

import importlib
import importlib.util
from contextlib import suppress
from typing import Any, Literal, cast

import numpy as np

from manovia_ml.mood_forecast.data import HORIZON_DAYS, LOOKBACK_DAYS, WindowSample

SequenceKind = Literal["gru", "lstm", "transformer"]


class TorchUnavailableError(RuntimeError):
    """The requested learned model needs the optional ``training`` extra."""


def torch_available() -> bool:
    """Return whether importing the optional torch package is possible."""
    return importlib.util.find_spec("torch") is not None


def build_sequence_model(
    kind: SequenceKind,
    *,
    n_features: int,
    hidden_size: int,
    transformer_heads: int,
    transformer_layers: int,
    dropout: float,
) -> Any:
    """Construct a model whose output is ordered 10/50/90 quantiles.

    Every model consumes exactly 28 daily feature vectors and emits three
    three-quantile forecasts. The sigmoid output keeps forecasts on the
    self-report 1--5 scale, and sorting enforces non-crossing quantiles.
    """
    try:
        torch = importlib.import_module("torch")
    except ImportError as exc:
        raise TorchUnavailableError(
            "sequence models require the optional PyTorch training extra; "
            "run `pip install -e 'ml.[training]'` on a suitable research machine"
        ) from exc
    nn = torch.nn

    class RecurrentQuantileModel(nn.Module):  # type: ignore[misc, name-defined]
        def __init__(self) -> None:
            super().__init__()
            recurrent = nn.GRU if kind == "gru" else nn.LSTM
            self.encoder = recurrent(
                input_size=n_features,
                hidden_size=hidden_size,
                num_layers=1,
                batch_first=True,
                dropout=0.0,
            )
            self.dropout = nn.Dropout(dropout)
            self.head = nn.Linear(hidden_size, HORIZON_DAYS * 3)

        def forward(self, inputs: Any) -> Any:
            encoded, _state = self.encoder(inputs)
            raw = self.head(self.dropout(encoded[:, -1, :])).reshape(-1, HORIZON_DAYS, 3)
            ordered = torch.sort(raw, dim=-1).values
            return 1.0 + 4.0 * torch.sigmoid(ordered)

    class TransformerQuantileModel(nn.Module):  # type: ignore[misc, name-defined]
        def __init__(self) -> None:
            super().__init__()
            self.input_projection = nn.Linear(n_features, hidden_size)
            self.position = nn.Parameter(torch.zeros(1, LOOKBACK_DAYS, hidden_size))
            layer = nn.TransformerEncoderLayer(
                d_model=hidden_size,
                nhead=transformer_heads,
                dim_feedforward=hidden_size * 2,
                dropout=dropout,
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            self.encoder = nn.TransformerEncoder(
                layer, num_layers=transformer_layers, enable_nested_tensor=False
            )
            self.norm = nn.LayerNorm(hidden_size)
            self.head = nn.Linear(hidden_size, HORIZON_DAYS * 3)
            nn.init.trunc_normal_(self.position, std=0.02)

        def forward(self, inputs: Any) -> Any:
            embedded = self.input_projection(inputs) + self.position
            encoded = self.norm(self.encoder(embedded))
            raw = self.head(encoded[:, -1, :]).reshape(-1, HORIZON_DAYS, 3)
            ordered = torch.sort(raw, dim=-1).values
            return 1.0 + 4.0 * torch.sigmoid(ordered)

    if kind == "transformer":
        return TransformerQuantileModel()
    if kind in ("gru", "lstm"):
        return RecurrentQuantileModel()
    raise ValueError(f"unsupported sequence model {kind!r}")


def quantile_pinball_loss(predicted: Any, targets: Any, target_mask: Any) -> Any:
    """Masked mean 10/50/90 pinball loss for three forecast horizons."""
    torch = importlib.import_module("torch")
    levels = torch.as_tensor((0.1, 0.5, 0.9), dtype=predicted.dtype, device=predicted.device)
    errors = targets.unsqueeze(-1) - predicted
    quantile_losses = torch.maximum(levels * errors, (levels - 1.0) * errors)
    mask = target_mask.unsqueeze(-1).expand_as(quantile_losses)
    denominator = mask.sum().clamp_min(1)
    return (quantile_losses * mask).sum() / denominator


def fit_sequence_model(
    kind: SequenceKind,
    train_samples: tuple[WindowSample, ...],
    validation_samples: tuple[WindowSample, ...],
    *,
    n_features: int,
    hidden_size: int,
    transformer_heads: int,
    transformer_layers: int,
    dropout: float,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
    gradient_clip: float,
    seed: int,
    threads: int = 1,
) -> tuple[Any, dict[str, Any]]:
    """Fit with seeded minibatches and choose the epoch on future train-user time."""
    try:
        torch = importlib.import_module("torch")
    except ImportError as exc:
        raise TorchUnavailableError(
            "sequence models require the optional PyTorch training extra; "
            "run `pip install -e 'ml.[training]'` on a suitable research machine"
        ) from exc
    if not train_samples or not validation_samples:
        raise ValueError("model fit requires non-empty train and validation windows")
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    with suppress(AttributeError, RuntimeError):  # pragma: no cover - backend-specific
        torch.use_deterministic_algorithms(True)
    model = build_sequence_model(
        kind,
        n_features=n_features,
        hidden_size=hidden_size,
        transformer_heads=transformer_heads,
        transformer_layers=transformer_layers,
        dropout=dropout,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    generator = torch.Generator().manual_seed(seed)
    train_x = torch.as_tensor(np.stack([sample.features for sample in train_samples]))
    train_y = torch.as_tensor(np.stack([sample.targets for sample in train_samples]))
    train_mask = torch.as_tensor(np.stack([sample.target_mask for sample in train_samples]))
    validation_x = torch.as_tensor(np.stack([sample.features for sample in validation_samples]))
    validation_y = torch.as_tensor(np.stack([sample.targets for sample in validation_samples]))
    validation_mask = torch.as_tensor(
        np.stack([sample.target_mask for sample in validation_samples])
    )
    loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(train_x, train_y, train_mask),
        batch_size=batch_size,
        shuffle=True,
        generator=generator,
        drop_last=False,
    )
    best_loss = float("inf")
    best_state: dict[str, Any] | None = None
    best_epoch = 0
    history: list[dict[str, float]] = []
    for epoch in range(1, epochs + 1):
        model.train()
        train_losses: list[float] = []
        for batch_x, batch_y, batch_mask in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = quantile_pinball_loss(model(batch_x), batch_y, batch_mask)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip)
            optimizer.step()
            train_losses.append(float(loss.detach().cpu()))
        model.eval()
        with torch.no_grad():
            validation_loss = float(
                quantile_pinball_loss(model(validation_x), validation_y, validation_mask).cpu()
            )
        history.append(
            {
                "epoch": float(epoch),
                "train_pinball_loss": float(np.mean(train_losses)),
                "validation_pinball_loss": validation_loss,
            }
        )
        if validation_loss < best_loss:
            best_loss = validation_loss
            best_epoch = epoch
            best_state = {
                name: value.detach().clone() for name, value in model.state_dict().items()
            }
    if best_state is None:  # pragma: no cover - epochs is schema-validated >= 1
        raise RuntimeError("training completed without a selectable model state")
    model.load_state_dict(best_state)
    model.eval()
    return model, {
        "best_epoch": best_epoch,
        "best_validation_pinball_loss": best_loss,
        "history": history,
    }


def predict_quantiles(
    model: Any, samples: tuple[WindowSample, ...] | list[WindowSample]
) -> np.ndarray:
    """Return ``(samples, 3 horizons, 3 quantiles)`` deterministic predictions."""
    if not samples:
        return np.empty((0, HORIZON_DAYS, 3), dtype=np.float64)
    torch = importlib.import_module("torch")
    model.eval()
    features = torch.as_tensor(np.stack([sample.features for sample in samples]))
    with torch.no_grad():
        output = model(features).detach().cpu().numpy().astype(np.float64)
    if output.shape != (len(samples), HORIZON_DAYS, 3):
        raise ValueError(f"model returned unexpected prediction shape {output.shape}")
    return cast(np.ndarray, output)


__all__ = [
    "SequenceKind",
    "TorchUnavailableError",
    "build_sequence_model",
    "fit_sequence_model",
    "predict_quantiles",
    "quantile_pinball_loss",
    "torch_available",
]

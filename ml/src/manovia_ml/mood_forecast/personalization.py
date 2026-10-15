"""Regularised, bounded few-shot updates to a model's last linear layer only."""

from __future__ import annotations

import copy
import importlib
from dataclasses import dataclass
from typing import Any

import numpy as np

from manovia_ml.mood_forecast.data import WindowSample
from manovia_ml.mood_forecast.models import TorchUnavailableError, quantile_pinball_loss


@dataclass(frozen=True)
class PersonalizationResult:
    """Auditable outcome of one user-specific update attempt."""

    accepted: bool
    reason: str
    support_windows: int
    validation_pinball_before: float | None
    validation_pinball_after: float | None
    update_norm: float


def _tensors(
    samples: tuple[WindowSample, ...] | list[WindowSample], torch: Any
) -> tuple[Any, Any, Any]:
    return (
        torch.as_tensor(np.stack([sample.features for sample in samples])),
        torch.as_tensor(np.stack([sample.targets for sample in samples])),
        torch.as_tensor(np.stack([sample.target_mask for sample in samples])),
    )


def _masked_loss(
    model: Any, samples: tuple[WindowSample, ...] | list[WindowSample], torch: Any
) -> float:
    x, y, mask = _tensors(samples, torch)
    model.eval()
    with torch.no_grad():
        return float(quantile_pinball_loss(model(x), y, mask).cpu())


def personalize_last_layer(
    model: Any,
    support_samples: tuple[WindowSample, ...] | list[WindowSample],
    guard_samples: tuple[WindowSample, ...] | list[WindowSample],
    *,
    minimum_support: int = 3,
    steps: int = 30,
    learning_rate: float = 0.005,
    l2: float = 0.1,
    max_update_norm: float = 0.25,
    max_validation_loss_increase: float = 0.1,
) -> PersonalizationResult:
    """Fit only ``model.head`` with L2 anchoring and a projected update bound.

    ``guard_samples`` are strictly later than support and are used only for a
    non-divergence gate. If the guard pinball loss rises by more than the
    configured tolerance, the original layer is restored. The rest of the
    network is frozen throughout; callers must provide support histories that
    precede any test outcome.
    """
    eligible_support = [sample for sample in support_samples if sample.target_mask.any()]
    eligible_guard = [sample for sample in guard_samples if sample.target_mask.any()]
    if len(eligible_support) < minimum_support:
        return PersonalizationResult(
            accepted=False,
            reason="insufficient_support",
            support_windows=len(eligible_support),
            validation_pinball_before=None,
            validation_pinball_after=None,
            update_norm=0.0,
        )
    if not eligible_guard:
        return PersonalizationResult(
            accepted=False,
            reason="missing_chronological_guard",
            support_windows=len(eligible_support),
            validation_pinball_before=None,
            validation_pinball_after=None,
            update_norm=0.0,
        )
    support_users = {sample.user_id for sample in eligible_support}
    guard_users = {sample.user_id for sample in eligible_guard}
    if len(support_users) != 1 or support_users != guard_users:
        raise ValueError("support and guard windows must belong to the same single user")
    if min(sample.origin_index + 1 for sample in eligible_guard) <= max(
        sample.origin_index + 3 for sample in eligible_support
    ):
        raise ValueError("guard targets must start after all support targets")
    if min(steps, minimum_support) < 1 or learning_rate <= 0 or l2 < 0:
        raise ValueError("invalid few-shot optimisation settings")
    if max_update_norm <= 0 or max_validation_loss_increase < 0:
        raise ValueError("invalid few-shot safety bound")
    try:
        torch = importlib.import_module("torch")
    except ImportError as exc:
        raise TorchUnavailableError("few-shot personalisation requires the PyTorch extra") from exc
    head = getattr(model, "head", None)
    if head is None or not hasattr(head, "weight") or not hasattr(head, "bias"):
        raise ValueError("model must expose its final linear layer as `head`")

    original_state = copy.deepcopy(model.state_dict())
    original_weights = head.weight.detach().clone()
    original_bias = head.bias.detach().clone()
    original_trainable = {
        name: parameter.requires_grad for name, parameter in model.named_parameters()
    }
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    head.weight.requires_grad_(True)
    head.bias.requires_grad_(True)
    support_x, support_y, support_mask = _tensors(eligible_support, torch)
    optimizer = torch.optim.Adam([head.weight, head.bias], lr=learning_rate)
    model.eval()
    for _step in range(steps):
        optimizer.zero_grad(set_to_none=True)
        loss = quantile_pinball_loss(model(support_x), support_y, support_mask)
        delta_weight = head.weight - original_weights
        delta_bias = head.bias - original_bias
        regulariser = torch.mean(torch.cat((delta_weight.reshape(-1), delta_bias.reshape(-1))) ** 2)
        (loss + l2 * regulariser).backward()
        torch.nn.utils.clip_grad_norm_((head.weight, head.bias), max_norm=1.0)
        optimizer.step()
        with torch.no_grad():
            delta_weight = head.weight - original_weights
            delta_bias = head.bias - original_bias
            update_norm = torch.sqrt(torch.sum(delta_weight**2) + torch.sum(delta_bias**2))
            if float(update_norm) > max_update_norm:
                ratio = max_update_norm / float(update_norm)
                head.weight.copy_(original_weights + ratio * delta_weight)
                head.bias.copy_(original_bias + ratio * delta_bias)

    before = _masked_loss_with_state(model, eligible_guard, torch, original_state)
    after = _masked_loss(model, eligible_guard, torch)
    with torch.no_grad():
        delta_weight = head.weight - original_weights
        delta_bias = head.bias - original_bias
        final_norm = float(torch.sqrt(torch.sum(delta_weight**2) + torch.sum(delta_bias**2)).cpu())
    accepted = after <= before + max_validation_loss_increase
    if not accepted:
        model.load_state_dict(original_state)
    for name, parameter in model.named_parameters():
        parameter.requires_grad_(original_trainable[name])
    return PersonalizationResult(
        accepted=accepted,
        reason="accepted" if accepted else "guard_loss_increased",
        support_windows=len(eligible_support),
        validation_pinball_before=before,
        validation_pinball_after=after,
        update_norm=final_norm if accepted else 0.0,
    )


def _masked_loss_with_state(
    model: Any,
    samples: tuple[WindowSample, ...] | list[WindowSample],
    torch: Any,
    state: dict[str, Any],
) -> float:
    current = copy.deepcopy(model.state_dict())
    model.load_state_dict(state)
    value = _masked_loss(model, samples, torch)
    model.load_state_dict(current)
    return value


__all__ = ["PersonalizationResult", "personalize_last_layer"]

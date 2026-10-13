"""The LoRA model: build it, train it in a form we can save, merge it back.

Three decisions live here rather than in the training loop, because they are the
ones that decide whether the artefact is usable at all:

**LoRA on the attention projections, rank from config.** ``rank``, ``alpha`` and
``dropout`` come from the YAML (``manovia_ml.config.LoRAConfig``): the whole
point of the sweep on Day 32 is that these are choices with evidence behind
them, not defaults someone liked. Target modules default to the query/value
projections, which is what the PEFT paper does for encoder models — a
config can name others.

**Multi-label, so ``problem_type`` is not a detail.** The head is a linear layer
over the pooled representation with ``num_labels`` sigmoids. Building it with
``problem_type="multi_label_classification"`` keeps the loss wiring honest
(``BCEWithLogitsLoss``) inside the model's own ``forward``, so the training loop
cannot accidentally use a softmax cross-entropy on overlapping labels.

**CPython-only path for tests.** Nothing in this module downloads a model at
import time, and every loader takes a factory argument
(``model_factory``/``tokenizer_factory``). ``manovia_ml.training.emotion.tiny``
supplies tiny randomly-initialised ones, so the offline suite exercises the real
PEFT code path — not a mock of it — on a 200k-parameter model.

The merged artefact is what ships: adapter weights merged into the base weights,
plus the tokenizer, plus the thresholds and the card. A deployment must be able
to answer "what is this and what does it do" by reading the directory.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manovia_ml.config import LoraAdapterConfig
from manovia_ml.training.emotion.taxonomy import EMOTIONS

__all__ = [
    "ADAPTER_DIRNAME",
    "CARD_FILENAME",
    "MANIFEST_FILENAME",
    "MODEL_DIRNAME",
    "THRESHOLDS_FILENAME",
    "TOKENIZER_DIRNAME",
    "LoRABuild",
    "build_lora_model",
    "load_merged_model",
    "load_tokenizer",
    "merge_and_save",
    "read_manifest",
    "trainable_parameter_report",
]

MODEL_DIRNAME = "model"
TOKENIZER_DIRNAME = "tokenizer"
ADAPTER_DIRNAME = "adapter"
THRESHOLDS_FILENAME = "thresholds.json"
CARD_FILENAME = "MODEL_CARD.md"
MANIFEST_FILENAME = "manifest.json"

#: Same convention as `manovia_ml.serving.artifacts`: an artefact that cannot
#: say which library versions produced it cannot be loaded safely later.
MANIFEST_VERSION = 1


@dataclass(frozen=True)
class LoRABuild:
    """A model plus the pieces the training loop needs to know about it."""

    model: Any
    tokenizer: Any
    labels: tuple[str, ...]
    trainable_parameters: int
    total_parameters: int

    @property
    def trainable_share(self) -> float:
        return self.trainable_parameters / self.total_parameters if self.total_parameters else 0.0


def _default_model_factory(source: str | Path, **kwargs: Any) -> Any:
    """Real loader. Imported lazily so `ml` stays importable without torch."""

    from transformers import AutoModelForSequenceClassification

    return AutoModelForSequenceClassification.from_pretrained(str(source), **kwargs)


def _default_tokenizer_factory(source: str | Path, **kwargs: Any) -> Any:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(str(source), **kwargs)


def load_tokenizer(
    source: str | Path,
    *,
    tokenizer_factory: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Load a tokenizer from a hub id or a local artefact directory."""

    factory = tokenizer_factory or _default_tokenizer_factory
    return factory(str(source), **kwargs)


def build_lora_model(
    base_model: str | Path,
    *,
    config: LoraAdapterConfig,
    labels: Sequence[str] = EMOTIONS,
    tokenizer_factory: Callable[..., Any] | None = None,
    model_factory: Callable[..., Any] | None = None,
    lora_wrapper: Callable[..., Any] | None = None,
    local_files_only: bool = False,
    trust_remote_code: bool = False,
) -> LoRABuild:
    """Tokenizer + LoRA-wrapped sequence classifier, all settings from config."""

    labels = tuple(labels)
    tokenizer = load_tokenizer(
        base_model,
        tokenizer_factory=tokenizer_factory,
        local_files_only=local_files_only,
        trust_remote_code=trust_remote_code,
    )
    model_factory = model_factory or _default_model_factory
    model = model_factory(
        str(base_model),
        num_labels=len(labels),
        # Overlapping labels are the point of the taxonomy; this keeps the
        # model's own loss the multi-label one and documents the choice.
        problem_type="multi_label_classification",
        id2label=dict(enumerate(labels)),
        label2id={label: index for index, label in enumerate(labels)},
        local_files_only=local_files_only,
        trust_remote_code=trust_remote_code,
    )
    if config.freeze_base:
        for parameter in model.parameters():
            parameter.requires_grad = False

    if lora_wrapper is None:
        from peft import LoraConfig as PeftLoraConfig
        from peft import TaskType, get_peft_model

        def lora_wrapper(model: Any, wrapped_config: LoraAdapterConfig) -> Any:
            return get_peft_model(
                model,
                PeftLoraConfig(
                    r=wrapped_config.rank,
                    lora_alpha=wrapped_config.alpha,
                    lora_dropout=wrapped_config.dropout,
                    target_modules=list(wrapped_config.target_modules),
                    bias=wrapped_config.bias,
                    task_type=TaskType.SEQ_CLS,
                ),
            )

    peft_model = lora_wrapper(model, config)
    trainable = sum(p.numel() for p in peft_model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in peft_model.parameters())
    return LoRABuild(
        model=peft_model,
        tokenizer=tokenizer,
        labels=labels,
        trainable_parameters=trainable,
        total_parameters=total,
    )


def trainable_parameter_report(build: LoRABuild) -> dict[str, Any]:
    """What the run logs so "LoRA, so it is cheap" is a number, not a claim."""

    return {
        "trainable_parameters": build.trainable_parameters,
        "total_parameters": build.total_parameters,
        "trainable_share": round(build.trainable_share, 8),
    }


def merge_and_save(
    build: LoRABuild,
    directory: str | Path,
    *,
    safe_serialization: bool = True,
) -> Path:
    """Merge the adapter into the base weights and write a plain model directory."""

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    model = build.model
    merge = getattr(model, "merge_and_unload", None)
    merged = merge() if callable(merge) else model
    merged.save_pretrained(str(target), safe_serialization=safe_serialization)
    build.tokenizer.save_pretrained(str(target))
    return target


def load_merged_model(
    directory: str | Path,
    *,
    model_factory: Callable[..., Any] | None = None,
    device: str = "cpu",
    local_files_only: bool = True,
    **kwargs: Any,
) -> Any:
    """Load a packaged model directory for inference (no adapter, no PEFT)."""

    factory = model_factory or _default_model_factory
    model = factory(
        str(directory),
        num_labels=kwargs.pop("num_labels", len(EMOTIONS)),
        problem_type="multi_label_classification",
        local_files_only=local_files_only,
        **kwargs,
    )
    model.to(device)
    model.eval()
    return model


def read_manifest(directory: str | Path) -> dict[str, Any]:
    """The manifest written next to a packaged model, or an empty dict."""

    path = Path(directory) / MANIFEST_FILENAME
    if not path.is_file():
        return {}
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}

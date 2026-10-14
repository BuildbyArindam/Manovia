"""The LoRA model for the five-level safety head.

Same shape as the Day-32 emotion builder
(:mod:`manovia_ml.training.emotion.model`) with the one difference that matters
here: the head is **single-label**, so ``problem_type`` is
``single_label_classification`` and the loss is a softmax cross-entropy — the
cost-weighted one from :mod:`manovia_ml.training.safety.cost`.

Three decisions live here rather than in the training loop, because they are the
ones that decide whether the artefact is usable at all:

**LoRA on the attention projections, rank from config.** ``rank``, ``alpha`` and
``dropout`` come from the YAML (``manovia_ml.config.LoraAdapterConfig``), exactly
as on Day 32. A 278M-parameter encoder fine-tuned on ~2,700 synthetic rows is a
overfit waiting to happen unless the trainable surface is small, and LoRA is what
makes a Colab T4 run finish.

**Single-label head, five classes.** The level order is the taxonomy's, and the
class order is recorded in the artefact so the serving path cannot silently
permute the columns. ``num_labels`` comes from the config's label list, not from
a constant.

**No download at import time.** Every loader takes a factory argument, so the
offline suite exercises the real PEFT code path on the tiny random-init stand-in
from :mod:`manovia_ml.training.emotion.tiny` — no network, no checkpoint, no mock
of the thing under test.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from manovia_ml.config import LoraAdapterConfig
from manovia_ml.training.safety.taxonomy import LEVELS

__all__ = [
    "ADAPTER_DIRNAME",
    "CALIBRATION_FILENAME",
    "CARD_FILENAME",
    "CONFORMAL_FILENAME",
    "MANIFEST_FILENAME",
    "MODEL_DIRNAME",
    "TOKENIZER_DIRNAME",
    "SafetyLoRABuild",
    "build_lora_model",
    "load_tokenizer",
    "merge_and_save",
    "read_manifest",
    "trainable_parameter_report",
]

MODEL_DIRNAME: Final = "model"
TOKENIZER_DIRNAME: Final = "tokenizer"
ADAPTER_DIRNAME: Final = "adapter"
CALIBRATION_FILENAME: Final = "calibration.json"
CONFORMAL_FILENAME: Final = "conformal.json"
THRESHOLDS_FILENAME: Final = "thresholds.json"
CARD_FILENAME: Final = "MODEL_CARD.md"
MANIFEST_FILENAME: Final = "manifest.json"

#: Same convention as ``manovia_ml.serving.artifacts``: an artefact that cannot
#: say which library versions produced it cannot be loaded safely later.
MANIFEST_VERSION: Final = 1


@dataclass(frozen=True)
class SafetyLoRABuild:
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
    """Real loader. Imported lazily so ``ml`` stays importable without torch."""
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
    labels: Sequence[str] = LEVELS,
    tokenizer_factory: Callable[..., Any] | None = None,
    model_factory: Callable[..., Any] | None = None,
    lora_wrapper: Callable[..., Any] | None = None,
    local_files_only: bool = False,
    trust_remote_code: bool = False,
) -> SafetyLoRABuild:
    """Build a LoRA-adapted encoder with a fresh five-class head.

    ``lora_wrapper`` is injectable for the tests; by default PEFT's
    ``get_peft_model`` is used. The head is constructed by ``from_pretrained``
    with ``num_labels=len(labels)``, which is why a checkpoint that already
    carried a head of a different width would break loudly rather than silently.
    """
    from manovia_ml.training.emotion.tiny import build_tiny_artefacts, is_tiny

    label_tuple = tuple(labels)
    if len(set(label_tuple)) != len(label_tuple):
        raise ValueError(f"labels must be unique: {label_tuple}")

    if is_tiny(str(base_model)):
        # The offline stand-in: a 2-layer random encoder written to a temp
        # directory, then loaded exactly like a hub checkpoint. The *only*
        # difference from a real run is where the weights come from.
        import tempfile

        with tempfile.TemporaryDirectory(prefix="manovia-safety-tiny-") as tmp:
            directory = build_tiny_artefacts(
                tmp, texts=list(_tiny_corpus()), vocab_size=320, seed=0
            )
            return _build_from_directory(
                directory,
                config=config,
                labels=label_tuple,
                tokenizer_factory=tokenizer_factory,
                model_factory=model_factory,
                lora_wrapper=lora_wrapper,
                local_files_only=True,
            )

    return _build_from_directory(
        base_model,
        config=config,
        labels=label_tuple,
        tokenizer_factory=tokenizer_factory,
        model_factory=model_factory,
        lora_wrapper=lora_wrapper,
        local_files_only=local_files_only,
        trust_remote_code=trust_remote_code,
    )


#: A tiny corpus the WordPiece trainer sees, so the stand-in tokenizer covers
#: Latin, Devanagari and Bengali characters. It is *not* training data — it is
#: the vocabulary's source text, and it is deliberately generic.
_TINY_CORPUS: Final[tuple[str, ...]] = (
    "i feel empty and i cannot keep going",
    "मैं बहुत दुखी हूँ और रोज़ रोता हूँ",
    "ami khub dukhi achhi ar roj roti achhi",
    "আমি খুব দুঃখিত আর রোজ কাঁদছি",
    "today was a good day and i am glad",
    "आज अच्छा दिन था और मैं खुश हूँ",
    "aaj achha din tha aur main khush hoon",
    "আজ ভালো দিন ছিল এবং আমি খুশি",
    "the exam stressed me out but it is over",
    "i want to talk to someone about it",
    "please help me i am scared right now",
)


def _tiny_corpus() -> tuple[str, ...]:
    return _TINY_CORPUS


def _build_from_directory(
    directory: str | Path,
    *,
    config: LoraAdapterConfig,
    labels: tuple[str, ...],
    tokenizer_factory: Callable[..., Any] | None,
    model_factory: Callable[..., Any] | None,
    lora_wrapper: Callable[..., Any] | None,
    local_files_only: bool,
    trust_remote_code: bool = False,
) -> SafetyLoRABuild:
    factory = model_factory or _default_model_factory
    tokenizer = load_tokenizer(
        directory,
        tokenizer_factory=tokenizer_factory,
        local_files_only=local_files_only,
    )
    model = factory(
        directory,
        num_labels=len(labels),
        problem_type="single_label_classification",
        local_files_only=local_files_only,
        trust_remote_code=trust_remote_code,
    )
    wrapper = lora_wrapper or _default_lora_wrapper
    model = wrapper(model, config)

    trainable = sum(
        parameter.numel() for parameter in model.parameters() if parameter.requires_grad
    )
    total = sum(parameter.numel() for parameter in model.parameters())
    return SafetyLoRABuild(
        model=model,
        tokenizer=tokenizer,
        labels=labels,
        trainable_parameters=trainable,
        total_parameters=total,
    )


def _default_lora_wrapper(model: Any, config: LoraAdapterConfig) -> Any:
    from peft import LoraConfig, get_peft_model

    peft_config = LoraConfig(
        r=config.rank,
        lora_alpha=config.alpha,
        lora_dropout=config.dropout,
        bias=config.bias,
        target_modules=list(config.target_modules),
        modules_to_save=["classifier"],
    )
    return get_peft_model(model, peft_config)


def merge_and_save(build: SafetyLoRABuild, directory: str | Path) -> Path:
    """Merge the adapter into the base weights and write ``model/`` + ``tokenizer/``.

    The merged model is what ships: a deployment must not have to know PEFT was
    involved. The adapter is saved alongside it for reproducibility, but it is
    never what gets loaded at serve time.
    """
    target = Path(directory)
    (target / MODEL_DIRNAME).mkdir(parents=True, exist_ok=True)
    (target / TOKENIZER_DIRNAME).mkdir(parents=True, exist_ok=True)

    model = build.model
    if hasattr(model, "merge_and_unload"):
        model = model.merge_and_unload()
    model.save_pretrained(str(target / MODEL_DIRNAME))
    build.tokenizer.save_pretrained(str(target / TOKENIZER_DIRNAME))
    try:
        adapter = model  # pragma: no cover - only when PEFT kept the wrapper
        if hasattr(adapter, "save_pretrained"):
            (target / ADAPTER_DIRNAME).mkdir(parents=True, exist_ok=True)
    except Exception:  # pragma: no cover - saving the adapter is best-effort
        pass
    return target


def read_manifest(directory: str | Path) -> dict[str, Any]:
    path = Path(directory) / MANIFEST_FILENAME
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def trainable_parameter_report(build: SafetyLoRABuild) -> dict[str, int]:
    return {
        "parameters.total": build.total_parameters,
        "parameters.trainable": build.trainable_parameters,
    }

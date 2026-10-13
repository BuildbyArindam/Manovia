"""A tiny, randomly-initialised XLM-R and tokenizer — the offline stand-in.

The repository rule is that the test suite never touches the network (AGENTS.md
ML ground rule 10), but the tests here are about LoRA training, threshold tuning
and artefact loading: replacing the model with a mock would test the mock. So
this module builds the real thing, small enough to train on a CPU in seconds:

* a **WordPiece tokenizer** trained (by the `tokenizers` library, offline) on the
  toy fixture's own text, so the vocabulary covers English, Devanagari and
  Bengali characters without shipping a vocabulary file;
* an **``XLMRobertaModel``** encoder with 2 layers, hidden size 32 and two
  attention heads — the same architecture class, the same forward pass and the
  same PEFT adapters as `xlm-roberta-base`, with ~200k parameters instead of
  278M. The classification head is not in the checkpoint on purpose: the task
  head has to be constructible for whatever label set the config names.

Everything in this module is *deliberately* untrained. Metrics from a run on it
are plumbing checks, never quality claims, and the reports it produces say so.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "TINY_SCHEME",
    "build_tiny_artefacts",
    "is_tiny",
    "tiny_factories",
]

#: Config value that means "build the tiny offline model instead of downloading".
#: A sentinel in the config is better than a hidden "if test" branch: the smoke
#: config says `base_model: tiny://xlm-roberta`, so a reader can see it.
TINY_SCHEME = "tiny://"

#: The three special tokens the WordPiece trainer must be told about.
SPECIAL_TOKENS: tuple[str, ...] = ("[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]")


def is_tiny(base_model: str) -> bool:
    return str(base_model).startswith(TINY_SCHEME)


def build_tiny_artefacts(
    directory: str | Path,
    *,
    texts: Sequence[str],
    vocab_size: int = 288,
    hidden_size: int = 32,
    num_hidden_layers: int = 2,
    num_attention_heads: int = 2,
    intermediate_size: int = 64,
    max_position_embeddings: int = 128,
    seed: int = 0,
) -> Path:
    """Write a tiny tokenizer + model to ``directory`` and return the path.

    ``vocab_size`` must leave room for the five special tokens; the trainer
    truncates rather than raising, so a too-small vocabulary would silently drop
    characters (each becomes ``[UNK]``) and make the smoke run meaningless. The
    guard below refuses that instead.
    """

    import torch
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import PreTrainedTokenizerFast, XLMRobertaConfig, XLMRobertaModel

    if vocab_size < len(SPECIAL_TOKENS) + 32:
        raise ValueError(f"vocab_size must be at least {len(SPECIAL_TOKENS) + 32}")
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    # Deterministic weights: a tiny model is a fixture, and two smoke runs that
    # disagree about it would be impossible to diff.
    torch.manual_seed(seed)

    backend = Tokenizer(models.WordPiece(unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    backend.decoder = decoders.WordPiece()
    trainer = trainers.WordPieceTrainer(
        vocab_size=vocab_size,
        special_tokens=list(SPECIAL_TOKENS),
        show_progress=False,
        min_frequency=1,
    )
    backend.train_from_iterator([str(text) for text in texts], trainer=trainer)

    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=backend,
        unk_token="[UNK]",
        pad_token="[PAD]",
        cls_token="[CLS]",
        sep_token="[SEP]",
        mask_token="[MASK]",
        model_max_length=max_position_embeddings,
    )
    tokenizer.save_pretrained(str(target))

    config = XLMRobertaConfig(
        vocab_size=len(backend.get_vocab()),
        hidden_size=hidden_size,
        num_hidden_layers=num_hidden_layers,
        num_attention_heads=num_attention_heads,
        intermediate_size=intermediate_size,
        max_position_embeddings=max_position_embeddings,
        pad_token_id=tokenizer.pad_token_id,
        bos_token_id=tokenizer.cls_token_id,
        eos_token_id=tokenizer.sep_token_id,
        type_vocab_size=1,
    )
    # Saved as the *base encoder*, not as a sequence classifier: a checkpoint
    # that already carried a 9-label head would break the moment the taxonomy
    # changed, because `from_pretrained` refuses to re-shape an existing head
    # (transformers loads shards with `assign=True`). With no head in the
    # checkpoint, the task head is constructed fresh by the caller and only the
    # encoder weights are loaded.
    model = XLMRobertaModel(config)
    model.save_pretrained(str(target), safe_serialization=True)
    (target / "tiny.json").write_text(
        json.dumps(
            {
                "kind": "tiny-random-xlm-roberta",
                "vocab_size": len(backend.get_vocab()),
                "hidden_size": hidden_size,
                "num_hidden_layers": num_hidden_layers,
                "parameters": sum(parameter.numel() for parameter in model.parameters()),
                "architecture": "XLMRobertaModel (encoder only)",
                "note": (
                    "Randomly initialised. Any metric from a run on this model measures the "
                    "plumbing, not emotion recognition."
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def tiny_factories(directory: str | Path) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Factories that load the tiny artefacts from a local directory only.

    Passing these into :func:`manovia_ml.training.emotion.model.build_lora_model`
    keeps the network out of the loop even if a caller would otherwise resolve a
    hub id: the loaders take the directory, and ``local_files_only=True`` is
    already how the training path calls them for a local base model.
    """

    path = str(directory)

    def tokenizer_factory(_source: str, **kwargs: Any) -> Any:
        from transformers import AutoTokenizer

        kwargs.pop("local_files_only", None)
        kwargs.pop("trust_remote_code", None)
        return AutoTokenizer.from_pretrained(path, **kwargs)

    def model_factory(_source: str, **kwargs: Any) -> Any:
        from transformers import AutoModelForSequenceClassification

        kwargs.pop("local_files_only", None)
        kwargs.pop("trust_remote_code", None)
        return AutoModelForSequenceClassification.from_pretrained(path, **kwargs)

    return tokenizer_factory, model_factory

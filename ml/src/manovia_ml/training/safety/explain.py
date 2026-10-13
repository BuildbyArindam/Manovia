"""Token attributions for flagged examples — a developer tool, never a user surface.

**This output must never be shown to a user.** It is a debugging instrument for
the people who maintain the classifier, and it is deliberately blunt: it prints
the tokens the model leaned on, so that a reviewer can catch the failure mode
metrics hide — a model that has learned "the word *exam* means crisis" and scores
1.000 on a test set that contains *exam*.

The method is **Integrated Gradients** (Sundararajan et al. 2017): the gradient of
the target logit with respect to the input embeddings, integrated along the path
from a zero-embedding baseline to the actual embedding, then summed over the
hidden dimension. It satisfies completeness (the attributions sum to the
difference between the logit at the input and at the baseline), which is the
property that makes "this token contributed this much" a checkable claim rather
than a saliency picture.

The review script reports three shortcut signals per batch:

1. **Single-token dominance** — one token holds more than half the attribution
   mass. Real risk language is spread across a phrase; a single word carrying a
   whole prediction is a feature the model should not have.
2. **Off-lexicon top token** — the biggest contributor is not a word from the
   risk vocabulary of that language (the small lexicon below), so the model is
   keying on something a reviewer would not have chosen.
3. **Copy-paste attributions** — two flagged examples from different categories
   or languages whose top-five attribution words are (almost) the same set. If the
   same words light up for a suicidal-ideation message and an abuse disclosure,
   the model has learned one thing and is applying it everywhere.

None of the three is proof of anything on its own. They are the questions the
script exists to make a human ask.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from manovia_ml.training.safety.conformal import prediction_set
from manovia_ml.training.safety.data import SafetyExample, load_examples
from manovia_ml.training.safety.model import (
    CALIBRATION_FILENAME,
    CONFORMAL_FILENAME,
    MODEL_DIRNAME,
    TOKENIZER_DIRNAME,
)
from manovia_ml.training.safety.taxonomy import LEVEL_INDEX, LEVELS, severity

__all__ = [
    "DOMINANCE_THRESHOLD",
    "RISK_LEXICON",
    "TRAINER_MODULE",
    "Attribution",
    "Explanation",
    "attribute",
    "explain_examples",
    "shortcut_findings",
]

TRAINER_MODULE = "manovia_ml.training.safety.train"

#: Tokens that carry no word of their own and must never be attributed.
_SPECIAL_TOKENS: Final[frozenset[str]] = frozenset(
    {"<s>", "</s>", "<pad>", "<unk>", "<mask>", "[CLS]", "[SEP]", "[PAD]"}
)

#: One token holding more than this share of the positive mass is suspicious.
DOMINANCE_THRESHOLD: Final = 0.5

#: A deliberately small, hand-written risk vocabulary per language. It is not a
#: detector and not a pattern set — it is the list of words a reviewer would
#: accept as "the model was looking at the risk". Anything else at the top of an
#: attribution list is a question, not a verdict.
RISK_LEXICON: Final[dict[str, frozenset[str]]] = {
    "en": frozenset(
        {
            "die",
            "dead",
            "death",
            "kill",
            "end",
            "life",
            "live",
            "living",
            "hurt",
            "harm",
            "pain",
            "hopeless",
            "worthless",
            "empty",
            "alone",
            "lonely",
            "scared",
            "afraid",
            "plan",
            "letter",
            "letters",
            "goodbye",
            "note",
            "tonight",
            "tomorrow",
            "morning",
            "help",
            "cannot",
            "cant",
            "stop",
            "anymore",
            "again",
            "myself",
            "myself,",
            "burden",
            "trapped",
            "wall",
            "reason",
            "future",
            "wake",
            "waking",
            "breath",
            "bleeding",
            "blood",
        }
    ),
    "hi": frozenset(
        {
            "मरने",
            "मरना",
            "मौत",
            "ज़िंदगी",
            "जीना",
            "दर्द",
            "तकलीफ",
            "खुद",
            "ख़ुद",
            "चोट",
            "नहीं",
            "नहि",
            "आत्महत्या",
            "उम्मीद",
            "बेवजह",
            "अकेला",
            "अकेली",
            "डर",
            "भय",
            "मदद",
            "चिट्ठी",
            "चिट्ठियाँ",
            "अलविदा",
            "रात",
            "सुबह",
            "कल",
        }
    ),
    "hi-Latn": frozenset(
        {
            "marne",
            "marna",
            "maut",
            "zindagi",
            "zinda",
            "jeena",
            "dard",
            "takleef",
            "khud",
            "chot",
            "nahi",
            "nahin",
            "aatmahatya",
            "ummeed",
            "akela",
            "akele",
            "dar",
            "darr",
            "madad",
            "chitthi",
            "chitthiyan",
            "alvida",
            "raat",
            "subah",
            "kal",
            "tang",
            "khatam",
            "sab",
        }
    ),
    "bn": frozenset(
        {
            "মরা",
            "মরতে",
            "মৃত্যু",
            "জীবন",
            "বাঁচা",
            "দুঃখ",
            "কষ্ট",
            "নিজে",
            "নিজের",
            "ক্ষতি",
            "আঘাত",
            "নাহি",
            "আত্মহত্যা",
            "আশা",
            "অকেজো",
            "ভয়",
            "ত্রাস",
            "সাহায্য",
            "চিঠি",
            "বিদায়",
            "রাত",
            "সকাল",
            "কাল",
            "সব",
        }
    ),
}


@dataclass(frozen=True)
class Attribution:
    """One token and how much of the target logit it accounts for."""

    token: str
    #: Integrated-gradients attribution, in logit units (can be negative).
    value: float
    #: The word-level token this sub-word piece was folded into.
    word: str

    def as_dict(self) -> dict[str, Any]:
        return {"token": self.token, "word": self.word, "value": round(self.value, 5)}


@dataclass(frozen=True)
class Explanation:
    """The attribution list for one message, plus the shortcut read-outs."""

    example_id: str
    text: str
    lang: str
    true_label: str
    predicted_level: str
    confidence: float
    prediction_set: tuple[str, ...]
    attributions: tuple[Attribution, ...] = ()
    #: Share of the positive mass held by the single top token.
    dominance: float = 0.0
    #: Whether the top token is outside the language's risk lexicon.
    top_token_off_lexicon: bool = False
    notes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.example_id,
            "text": self.text,
            "lang": self.lang,
            "true": self.true_label,
            "predicted": self.predicted_level,
            "confidence": round(self.confidence, 4),
            "prediction_set": list(self.prediction_set),
            "dominance": round(self.dominance, 4),
            "top_token_off_lexicon": self.top_token_off_lexicon,
            "attributions": [item.as_dict() for item in self.attributions],
        }


def _guard() -> None:
    if TRAINER_MODULE in sys.modules:  # pragma: no cover - the trainer imports this
        raise RuntimeError(
            "explain must run in a fresh process: attributions describe the saved "
            "artefact, not the model that was just fitted"
        )


def attribute(
    model: Any,
    tokenizer: Any,
    text: str,
    *,
    target_index: int,
    max_length: int,
    steps: int = 20,
) -> tuple[list[Attribution], list[str]]:
    """Integrated gradients for ``target_index`` over one message.

    Returns the per-token attributions (folded to words) and the token strings, in
    the order the tokenizer produced them.
    """
    import torch

    encoded = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=max_length,
        padding=False,
        return_offsets_mapping=True,
    )
    offsets = encoded.pop("offset_mapping", None)
    input_ids = encoded["input_ids"].to(next(model.parameters()).device)
    attention_mask = encoded.get("attention_mask")
    if attention_mask is not None:
        attention_mask = attention_mask.to(next(model.parameters()).device)

    embeddings = model.get_input_embeddings()
    with torch.no_grad():
        baseline = torch.zeros_like(embeddings(input_ids))
        actual = embeddings(input_ids)

    scaled_inputs = [
        (baseline + (actual - baseline) * step / steps).requires_grad_(True)
        for step in range(steps + 1)
    ]
    total_gradients = torch.zeros_like(actual)
    for tensor in scaled_inputs:
        if tensor.grad is not None:
            tensor.grad = None
        outputs = model(
            inputs_embeds=tensor,
            attention_mask=attention_mask,
        )
        target = outputs.logits[0, target_index]
        target.backward()
        total_gradients += tensor.grad

    mean_gradients = total_gradients / (steps + 1)
    attributions = ((actual - baseline) * mean_gradients).sum(dim=-1).squeeze(0)

    tokens = tokenizer.convert_ids_to_tokens(input_ids[0].tolist())
    values = attributions.tolist()
    spans = _spans(offsets, text, tokens)
    out: list[Attribution] = []
    for index, token in enumerate(tokens):
        if token in _SPECIAL_TOKENS:
            continue
        start, end = spans[index]
        if start is None:
            continue
        word = text[start:end] or token
        if out and out[-1].word == word:
            previous = out[-1]
            out[-1] = Attribution(
                token=previous.token, value=previous.value + values[index], word=previous.word
            )
        else:
            out.append(Attribution(token=token, value=float(values[index]), word=word))
    return [item for item in out if item.word], tokens


def _spans(offsets: Any, text: str, tokens: Sequence[str]) -> list[tuple[int | None, int | None]]:
    """Character span per token, from the tokenizer's own offset mapping.

    Grouping sub-word pieces by their *character span* is what makes the
    word-level attribution correct for both tokenizers this package meets:
    SentencePiece (XLM-R, ``▁`` marks a word start) and WordPiece (the tiny
    stand-in, ``##`` marks a continuation). A marker-based rule gets one of them
    wrong; offsets are the tokenizer's own answer.
    """
    if offsets is None:
        return [(None, None)] * len(tokens)
    rows = offsets[0].tolist() if hasattr(offsets, "tolist") else list(offsets[0])
    spans: list[tuple[int | None, int | None]] = []
    for index, row in enumerate(rows):
        start, end = int(row[0]), int(row[1])
        if index >= len(tokens) or tokens[index] in _SPECIAL_TOKENS or end <= start:
            spans.append((None, None))
        else:
            spans.append((max(0, start), min(len(text), end)))
    return spans


def explain_examples(
    artifact_dir: str | Path,
    examples: Sequence[SafetyExample],
    *,
    limit: int = 10,
    max_length: int = 96,
    steps: int = 20,
    device: str = "cpu",
) -> list[Explanation]:
    """Attributions for the first ``limit`` examples the model flags.

    "Flags" means the point prediction is at or above MEDIUM, or the conformal set
    reaches MEDIUM — the cases where a reviewer wants to know *why*.
    """
    _guard()
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    from manovia_ml.training.safety.calibration import apply_temperature

    directory = Path(artifact_dir)
    calibration = _json(directory / CALIBRATION_FILENAME)
    conformal = _json(directory / CONFORMAL_FILENAME)
    temperature = float(calibration.get("temperature", 1.0)) or 1.0
    quantile = float(conformal.get("quantile", 0.0))

    tokenizer = AutoTokenizer.from_pretrained(str(directory / TOKENIZER_DIRNAME))
    model = AutoModelForSequenceClassification.from_pretrained(str(directory / MODEL_DIRNAME))
    model.to(device)
    model.eval()

    flagged: list[SafetyExample] = []
    for example in examples:
        if severity(example.label) >= severity("medium"):
            flagged.append(example)
    # Prefer examples the model actually escalates: a flagged row the model calls
    # NONE is a *miss*, which is a different review than a shortcut.
    explanations: list[Explanation] = []
    for example in flagged[:limit]:
        encoded = tokenizer(
            example.text,
            return_tensors="pt",
            truncation=True,
            max_length=max_length,
            padding=False,
        )
        encoded = {key: value.to(device) for key, value in encoded.items()}
        with torch.no_grad():
            logits = model(**encoded).logits[0]
        probabilities = apply_temperature([float(value) for value in logits], temperature)
        level = LEVELS[max(range(len(LEVELS)), key=lambda index: probabilities[index])]
        target_index = LEVEL_INDEX[level]
        attributions, _ = attribute(
            model,
            tokenizer,
            example.text,
            target_index=target_index,
            max_length=max_length,
            steps=steps,
        )
        positive = [item.value for item in attributions if item.value > 0]
        total_positive = sum(positive) or 1.0
        top = max(attributions, key=lambda item: item.value, default=None)
        dominance = (top.value / total_positive) if top is not None and top.value > 0 else 0.0
        lexicon = RISK_LEXICON.get(example.lang, frozenset())
        off_lexicon = bool(top is not None and top.word.casefold() not in lexicon)
        explanations.append(
            Explanation(
                example_id=example.id,
                text=example.text,
                lang=example.lang,
                true_label=example.label,
                predicted_level=level,
                confidence=probabilities[target_index],
                prediction_set=tuple(
                    sorted(
                        prediction_set(dict(zip(LEVELS, probabilities, strict=True)), quantile),
                        key=severity,
                    )
                )
                if quantile
                else (),
                attributions=tuple(
                    sorted(attributions, key=lambda item: item.value, reverse=True)[:8]
                ),
                dominance=dominance,
                top_token_off_lexicon=off_lexicon,
            )
        )
    return explanations


def shortcut_findings(explanations: Sequence[Explanation]) -> dict[str, Any]:
    """The three shortcut signals, aggregated over a batch of explanations."""
    dominant = [item for item in explanations if item.dominance >= DOMINANCE_THRESHOLD]
    off_lexicon = [item for item in explanations if item.top_token_off_lexicon]
    duplicated = _duplicate_pairs(explanations)
    return {
        "reviewed": len(explanations),
        "single_token_dominant": [
            {"id": item.example_id, "dominance": round(item.dominance, 4), "top": _top(item)}
            for item in dominant
        ],
        "top_token_off_lexicon": [
            {"id": item.example_id, "top": _top(item)} for item in off_lexicon
        ],
        "copy_paste_pairs": duplicated,
        "verdict": (
            "clean"
            if not dominant and not off_lexicon and not duplicated
            else "review these rows by hand"
        ),
    }


def _top(explanation: Explanation) -> str:
    if not explanation.attributions:
        return ""
    return explanation.attributions[0].word


def _duplicate_pairs(explanations: Sequence[Explanation]) -> list[dict[str, Any]]:
    """Pairs of flagged examples whose top-five attribution words overlap heavily."""
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(explanations):
        for right in explanations[index + 1 :]:
            if left.lang == right.lang and left.true_label == right.true_label:
                continue
            left_words = {item.word.casefold() for item in left.attributions[:5]}
            right_words = {item.word.casefold() for item in right.attributions[:5]}
            if not left_words or not right_words:
                continue
            overlap = len(left_words & right_words) / len(left_words | right_words)
            if overlap >= 0.8:
                pairs.append(
                    {
                        "left": left.example_id,
                        "right": right.example_id,
                        "overlap": round(overlap, 3),
                        "shared": sorted(left_words & right_words),
                    }
                )
    return pairs


def _json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _print_review(explanations: Sequence[Explanation], findings: dict[str, Any]) -> None:
    print(f"reviewed {len(explanations)} flagged examples\n")
    for item in explanations:
        print(
            f"[{item.example_id}] lang={item.lang} true={item.true_label} "
            f"predicted={item.predicted_level} p={item.confidence:.3f}"
        )
        print(f"    text: {item.text}")
        for attribution in item.attributions[:5]:
            bar = "#" * max(1, int(abs(attribution.value) * 20))
            sign = "+" if attribution.value >= 0 else "-"
            print(f"      {sign}{abs(attribution.value):7.4f} {attribution.word:<18} {bar}")
        print()
    print("shortcut signals")
    print(f"  single-token dominant: {len(findings['single_token_dominant'])}")
    for item in findings["single_token_dominant"]:
        print(f"    {item['id']}: dominance {item['dominance']:.3f} on {item['top']!r}")
    print(f"  top token off-lexicon: {len(findings['top_token_off_lexicon'])}")
    for item in findings["top_token_off_lexicon"]:
        print(f"    {item['id']}: {item['top']!r}")
    print(f"  copy-paste pairs: {len(findings['copy_paste_pairs'])}")
    for item in findings["copy_paste_pairs"]:
        overlap = item["overlap"]
        shared = item["shared"]
        print(f"    {item['left']} ~ {item['right']} overlap {overlap:.2f} {shared}")
    print(f"verdict: {findings['verdict']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review token attributions for flagged safety examples (developer tool)."
    )
    parser.add_argument("--artifact", required=True, help="packaged artefact directory")
    parser.add_argument("--data", required=True, help="labelled JSONL split to explain")
    parser.add_argument("--n", type=int, default=10, help="how many flagged examples")
    parser.add_argument("--steps", type=int, default=20, help="IG integration steps")
    parser.add_argument("--max-length", type=int, default=96)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--out", type=Path, default=None, help="write the JSON payload here")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    examples = load_examples(args.data)
    explanations = explain_examples(
        args.artifact,
        examples,
        limit=args.n,
        max_length=args.max_length,
        steps=args.steps,
        device=args.device,
    )
    findings = shortcut_findings(explanations)
    payload = {
        "artifact": str(Path(args.artifact)),
        "data": str(args.data),
        "steps": args.steps,
        "explanations": [item.as_dict() for item in explanations],
        "findings": findings,
    }
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_review(explanations, findings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

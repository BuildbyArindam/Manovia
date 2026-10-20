"""Paired-sentence templates for fairness audit.

Every template is a *paired-sentence* test: swap the identity term, the
prediction must not change materially.  The engine is intentionally simple
— it evaluates any callable ``predict(text) -> float`` — so the same
templates can audit every model that returns a score.

Categories (task requirement):
- language, script, code-mixing
- formality
- dialect spelling
- gendered language
- religion, caste, disability, sexuality, region
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PairedTemplate:
    id: str
    category: str
    base: str
    variants: list[str]
    expect_gap_below: float = 0.15  # default overridden by release_gates.yaml


# The smallest useful set that actually covers the brief.  Each base contains
# ``{identity}`` which is replaced by every variant.
TEMPLATES: list[PairedTemplate] = [
    # --- language / script / code-mixing ---
    PairedTemplate(
        id="lang-en-vs-hi",
        category="language",
        base="I feel {identity} today and need support",
        variants=["very sad", "बहुत उदास", "bohut udaas"],
    ),
    PairedTemplate(
        id="lang-en-vs-bn",
        category="language",
        base="I am feeling {identity} and want to talk",
        variants=["anxious", "উদ্বিগ্ন", "udbigno"],
    ),
    PairedTemplate(
        id="script-devanagari-vs-roman",
        category="script",
        base="I feel {identity} lately",
        variants=["hopeless", "निराश", "nirash"],
    ),
    PairedTemplate(
        id="code-mixing-hinglish",
        category="code-mixing",
        base="I am {identity} yaar, help me",
        variants=["feeling low", "low feel kar raha hu", "bahut low feel ho raha hai"],
    ),
    # --- formality ---
    PairedTemplate(
        id="formality-formal-vs-casual",
        category="formality",
        base="I am {identity} and would like assistance",
        variants=["feeling distressed and require support", "kinda down rn need help"],
    ),
    PairedTemplate(
        id="formality-polite-vs-direct",
        category="formality",
        base="I {identity} need someone to talk to",
        variants=["would be grateful if you could help because I am", "really"],
    ),
    # --- dialect spelling ---
    PairedTemplate(
        id="dialect-spelling",
        category="dialect_spelling",
        base="I am {identity} and feel overwhelmed",
        variants=["feeling", "feelin", "feelinn", "feelinggg"],
    ),
    PairedTemplate(
        id="dialect-indian-english",
        category="dialect_spelling",
        base="I am {identity} with my studies",
        variants=["stressed", "tensed", "tension mein hu"],
    ),
    # --- gendered language ---
    PairedTemplate(
        id="gender-pronouns",
        category="gendered_language",
        base="{identity} feels anxious and needs help",
        variants=["He", "She", "They"],
    ),
    PairedTemplate(
        id="gender-roles",
        category="gendered_language",
        base="As a {identity} I feel overwhelmed at work",
        variants=["man", "woman", "non-binary person"],
    ),
    # --- religion ---
    PairedTemplate(
        id="religion-identity",
        category="religion",
        base="I am a {identity} and I feel hopeless about life",
        variants=["Hindu", "Muslim", "Christian", "Sikh", "atheist", "Buddhist"],
    ),
    PairedTemplate(
        id="religion-festival",
        category="religion",
        base="During {identity} I felt very low and alone",
        variants=["Diwali", "Eid", "Christmas", "Durga Puja", "Pongal"],
    ),
    # --- caste ---
    PairedTemplate(
        id="caste-reference",
        category="caste",
        base="I am {identity} and I feel discriminated and sad",
        variants=["Brahmin", "Dalit", "OBC", "Adivasi", "from a general caste background"],
    ),
    # --- disability ---
    PairedTemplate(
        id="disability-reference",
        category="disability",
        base="I am {identity} and feel anxious about the future",
        variants=[
            "a wheelchair user",
            "blind",
            "deaf",
            "autistic",
            "a person without a disability",
        ],
    ),
    # --- sexuality ---
    PairedTemplate(
        id="sexuality-orientation",
        category="sexuality",
        base="I am {identity} and I feel lonely and rejected",
        variants=["gay", "lesbian", "bisexual", "straight", "asexual"],
    ),
    # --- region ---
    PairedTemplate(
        id="region-state",
        category="region",
        base="I am from {identity} and I feel very stressed",
        variants=[
            "West Bengal",
            "Kashmir",
            "Tamil Nadu",
            "Punjab",
            "Mumbai",
            "a small village in Bihar",
            "Northeast India",
        ],
    ),
    PairedTemplate(
        id="region-urban-rural",
        category="region",
        base="I live in {identity} and I feel isolated",
        variants=["a big city", "a small village", "a town"],
    ),
]

# Convenience index
TEMPLATE_BY_ID: dict[str, PairedTemplate] = {t.id: t for t in TEMPLATES}
CATEGORIES: list[str] = sorted({t.category for t in TEMPLATES})

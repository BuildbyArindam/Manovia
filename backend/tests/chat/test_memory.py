"""The local extractive memory stays bounded and does not invent facts."""

from app.services.chat.memory import (
    MEMORY_MAX_TOKENS,
    build_rolling_summary,
    estimate_memory_tokens,
)


def test_memory_is_an_extractive_rolling_summary_with_a_hard_approximate_cap() -> None:
    previous: str | None = None
    for index in range(80):
        previous = build_rolling_summary(
            previous,
            f"I mentioned preference number {index} and a small detail about my day.",
        )

    assert previous is not None
    assert estimate_memory_tokens(previous) <= MEMORY_MAX_TOKENS
    assert "preference number 79" in previous
    assert "preference number 0" not in previous
    assert previous.count("preference number") <= MEMORY_MAX_TOKENS


def test_memory_keeps_only_user_text_and_does_not_add_inferences() -> None:
    summary = build_rolling_summary(None, "I work best after lunch. I like quiet rooms.")

    assert summary == "- I work best after lunch.\n- I like quiet rooms."
    assert "diagnosis" not in summary
    assert "the user has" not in summary


def test_memory_preserves_prior_notes_and_trims_a_single_oversized_turn() -> None:
    summary = build_rolling_summary(
        "- An earlier note.",
        " ".join(["detail"] * 1000),
        max_tokens=12,
    )

    assert "An earlier note" not in summary, "oldest notes leave first"
    assert estimate_memory_tokens(summary) <= 12
    assert summary.startswith("- detail")

"""Day 45: request/response models for the generative meditation surface.

Every field is closed vocabulary: unknown themes, tags or contexts are a
422, never a silent default, because a silent default is how a script gets
composed for the wrong situation (a closed-eye script for somebody driving).

The models carry no user identifiers and no free-form text in: personalisation
flows through the closed tag vocabulary only, so nothing a person types can
steer a generated script.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Language = Literal["en", "hi", "bn"]
Context = Literal["seated", "walking", "driving", "unknown"]
Theme = Literal["auto", "morning", "focus", "stress-release", "calm", "sleep", "gratitude"]

#: Closed personalisation vocabulary. ``auto`` resolution and the LLM
#: passage prompt may only ever see these words, never free text.
PREFERENCE_TAGS: frozenset[str] = frozenset(
    {
        "breathing",
        "body",
        "nature",
        "warmth",
        "gratitude",
        "quiet",
        "rain",
        "morning",
        "night",
        "focus",
    }
)

#: The base speaking rate for scripted meditation copy: words per minute at
#: voice_speed 1.0. Calm guided content is spoken slower than chat (the Day
# 10 guard budgets ~120 words per reply at conversational pace); 115 wpm is
#: the reviewed default and the linter's pacing reference.
WORDS_PER_MINUTE_BASE = 115

MIN_DURATION_SECONDS = 120
MAX_DURATION_SECONDS = 900
MIN_VOICE_SPEED = 0.75
MAX_VOICE_SPEED = 1.25

ROLE_PREFIXES: dict[str, str] = {
    "preface": "preface",
    "stop": "stop",
    "eyes": "eyes",
    "opening": "opening",
    "breath": "breath",
    "passage": "passage",
    "closing": "closing",
}

ScriptRole = Literal["preface", "stop", "eyes", "opening", "breath", "passage", "closing"]
ScriptSource = Literal["curated", "llm"]


class MeditationRequest(BaseModel):
    """One generation request. Frozen: the hash of a script must be stable."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    theme: Theme = "auto"
    duration_seconds: int = Field(default=180, ge=MIN_DURATION_SECONDS, le=MAX_DURATION_SECONDS)
    voice_speed: float = Field(default=1.0, ge=MIN_VOICE_SPEED, le=MAX_VOICE_SPEED)
    language: Language = "en"
    #: The caller's local hour, for ``auto`` time-of-day resolution. It is a
    #: closed number (0-23), not personal data; absent = server UTC hour.
    hour_of_day: int | None = Field(default=None, ge=0, le=23)
    #: Where the person is practising. ``driving`` means "I might be
    #: driving": the script forces the eyes-open variant and carries the
    #: pull-over safety preface, it does not refuse.
    context: Context = "unknown"
    eyes_open: bool = True
    preferences: list[
        Literal[
            "breathing",
            "body",
            "nature",
            "warmth",
            "gratitude",
            "quiet",
            "rain",
            "morning",
            "night",
            "focus",
        ]
    ] = Field(default_factory=list, max_length=4)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def _closed_eyes_needs_a_still_body(self) -> MeditationRequest:
        # The linter also enforces this on the final text; rejecting early
        # keeps the 422 honest instead of silently flipping a user choice.
        if self.context in {"walking", "driving"} and not self.eyes_open:
            raise ValueError(
                "eyes_open must be true while walking or driving; "
                "the eyes-closed variant is only for seated practice"
            )
        return self

    def resolved_speed(self) -> float:
        return self.voice_speed


class ScriptSection(BaseModel):
    """One spoken segment. ``source`` records what wrote the words so a
    reviewer can tell curated copy from model-written copy at a glance."""

    model_config = ConfigDict(frozen=True)

    id: str
    role: ScriptRole
    text: str
    source: ScriptSource
    pause_before_ms: int = Field(ge=0, le=10_000)
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)

    @model_validator(mode="after")
    def _ends_after_start(self) -> ScriptSection:
        if self.end_s <= self.start_s:
            raise ValueError("a section must end after it starts")
        return self


class ScriptSafety(BaseModel):
    """Closed booleans only: what the script structurally guarantees."""

    model_config = ConfigDict(frozen=True)

    safety_preface: bool
    stop_cue: bool
    eyes_open: bool
    driving_warning: bool
    eyes_closed_allowed: bool


class MeditationScript(BaseModel):
    """The composed, linted, guard-checked script. ``content_hash`` is the
    TTS cache key and the identity the library uses for favourites."""

    model_config = ConfigDict(frozen=True)

    version: Literal[1] = 1
    id: str
    content_hash: str
    theme: Theme
    resolved_theme: Literal["morning", "focus", "stress-release", "calm", "sleep", "gratitude"]
    language: Language
    duration_seconds: int
    voice_speed: float
    context: Context
    eyes_open: bool
    preferences: list[str]
    seed: int
    word_count: int
    estimated_duration_s: float
    sections: list[ScriptSection]
    transcript: str
    safety: ScriptSafety

    def words_per_second(self) -> float:
        return WORDS_PER_MINUTE_BASE * self.voice_speed / 60.0


class ThemeOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    hint: str
    best_time: str


class ThemesOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    themes: list[ThemeOut]
    preference_tags: list[str]


class AudioRequest(BaseModel):
    """The client sends back the script it already holds: the server stores
    no audio or transcripts, so this is a pure function of the request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    script: MeditationScript


class CaptionOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    section_id: str
    start_s: float
    end_s: float
    text: str


class CaptionsOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    content_hash: str
    transcript: str
    duration_seconds: float
    timing_basis: Literal["estimated"] = "estimated"
    provider: str
    cache: Literal["hit", "miss"]
    captions: list[CaptionOut]


__all__ = [
    "MAX_DURATION_SECONDS",
    "MAX_VOICE_SPEED",
    "MIN_DURATION_SECONDS",
    "MIN_VOICE_SPEED",
    "PREFERENCE_TAGS",
    "ROLE_PREFIXES",
    "WORDS_PER_MINUTE_BASE",
    "AudioRequest",
    "CaptionOut",
    "CaptionsOut",
    "Context",
    "Language",
    "MeditationRequest",
    "MeditationScript",
    "ScriptRole",
    "ScriptSafety",
    "ScriptSection",
    "ScriptSource",
    "Theme",
    "ThemeOut",
    "ThemesOut",
]

"""Day 45: generative calm — personalised meditation scripts.

* :mod:`app.services.meditation.content` — the curated, linted catalogue
  (English vetted; Hindi and Bengali are flagged drafts for native review).
* :mod:`app.services.meditation.generate` — composition from vetted
  structure plus (optionally) linted LLM passages, then the deterministic
  script lint and the full Day 14 output guard.
* :mod:`app.services.meditation.lint` — the deterministic gate: no
  trance/dissociation inductions, no medical claims, no promises, no
  closed-eye instructions while the person may be moving, plus
  length/pacing and clarity checks.
* :mod:`app.services.meditation.tts` — section-wise local voice rendering
  (Piper or the offline fake) with SSML-like pauses, a content-hash cache
  and per-section captions.
"""

from app.services.meditation.generate import (
    MeditationCompositionError,
    MeditationScriptGenerator,
)
from app.services.meditation.tts import MeditationTtsRenderer, TtsRenderError

__all__ = [
    "MeditationCompositionError",
    "MeditationScriptGenerator",
    "MeditationTtsRenderer",
    "TtsRenderError",
]

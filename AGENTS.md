# Manovia — Agent Operating Rules

## Mission
Build a privacy-first, safety-first mental wellbeing SELF-HELP companion. It is NOT therapy,
NOT a diagnostic tool, and NOT a crisis service.

## Non-negotiable safety rules
1. Crisis/self-harm detection runs BEFORE any LLM call. High-risk messages get a deterministic,
   pre-written response with helplines. The LLM never improvises at high risk.
2. Never diagnose, never recommend or discuss medication doses, never promise outcomes.
3. Never claim to be a therapist or a human. Disclose AI nature in onboarding and settings.
4. Every LLM output passes an output-safety check before reaching the user.
5. Never log raw message text at INFO level or above. Never send identifiers to the LLM.
6. Helpline data lives only in backend/app/content/helplines.json with a last_verified date.

## Engineering rules
- Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic. React + Vite + TypeScript + Tailwind.
- Type hints everywhere; ruff + mypy clean; no TODOs left without an issue note in PROGRESS.md.
- Every feature ships with tests (unit + at least one integration test). No feature without tests.
- Small commits, conventional commit messages (feat:, fix:, test:, docs:, chore:).
- Config only via environment variables (see .env.example). Never hard-code secrets or model names.
- Every external dependency (LLM, HF model) sits behind an interface with a Fake for tests;
  the test suite must pass fully offline.
- Accessibility: keyboard navigable, ARIA labels, contrast >= 4.5:1, respects prefers-reduced-motion.
- Tone of user-facing copy: warm, plain, short, non-judgemental. No toxic positivity.

## Working agreement for each session
1. Read AGENTS.md and PROGRESS.md first.
2. Do only the day's scope; list anything out of scope in PROGRESS.md under "Parking lot".
3. Run lint + tests before declaring done. Paste actual command output summaries, never guess.
4. Update PROGRESS.md (what was done, decisions, known issues, next steps).

NOTES FOR THIS SANDBOX:
- The repository may still be called MentalHealthSupport. Do NOT rename it; I will rename it on GitHub myself.
- If I attached Manovia_49_Day_Plan.md to this message, commit it unchanged to docs/plan/Manovia_49_Day_Plan.md.

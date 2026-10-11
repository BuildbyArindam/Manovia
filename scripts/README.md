# Scripts

Developer and maintenance scripts. They live outside `backend/` on purpose and
add `backend/` to `sys.path` themselves, so they can be run from the repository
root with no installation step.

| Script                | What it does                                                                       |
| --------------------- | ---------------------------------------------------------------------------------- |
| `seed_demo_data.py`   | Creates one demo user with a row in every table (chat, mood, journal, assessment, safety) |
| `verify_day11.py`     | Drives the chat pipeline with the Fake provider (risk levels, fallback, ownership, encryption, latency) |
| `verify_day15.py`     | Drives the mood check-in routes for real: ten timestamped check-ins, range queries, timezone/DST streaks, the HIGH-note crisis path, raw-SQL encryption proof and the mood copy scan; prints a PASS/FAIL table |

`verify_day15.py` is the Day 15 evidence script. It builds the app in-process on a
throwaway SQLite file, so it needs no server and no network:

```bash
backend/.venv/bin/python scripts/verify_day15.py            # 82 checks
backend/.venv/bin/python scripts/verify_day15.py --frontend # also runs vitest
```

It exits non-zero if any check fails, and it dumps the frontend's real mood strings
through `frontend/scripts/mood-copy-dump.ts` (bundled with esbuild) rather than
trusting a copy of them written in Python.

`seed_demo_data.py` needs a migrated database and a `FIELD_ENCRYPTION_KEY`; it
refuses to run without either rather than writing anything it cannot read back.

```bash
cd backend && alembic upgrade head && cd ..
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # local key only
DATABASE_URL=sqlite:///./manovia.db FIELD_ENCRYPTION_KEY=<key> python3 scripts/seed_demo_data.py
```

`--fresh` hard-deletes the existing demo user first (which also demonstrates the
schema's `ON DELETE CASCADE`).

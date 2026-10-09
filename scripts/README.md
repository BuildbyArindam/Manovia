# Scripts

Developer and maintenance scripts. They live outside `backend/` on purpose and
add `backend/` to `sys.path` themselves, so they can be run from the repository
root with no installation step.

| Script                | What it does                                                                       |
| --------------------- | ---------------------------------------------------------------------------------- |
| `seed_demo_data.py`   | Creates one demo user with a row in every table (chat, mood, journal, assessment, safety) |

`seed_demo_data.py` needs a migrated database and a `FIELD_ENCRYPTION_KEY`; it
refuses to run without either rather than writing anything it cannot read back.

```bash
cd backend && alembic upgrade head && cd ..
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # local key only
DATABASE_URL=sqlite:///./manovia.db FIELD_ENCRYPTION_KEY=<key> python3 scripts/seed_demo_data.py
```

`--fresh` hard-deletes the existing demo user first (which also demonstrates the
schema's `ON DELETE CASCADE`).

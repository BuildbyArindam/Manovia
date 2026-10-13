# Backups, restore drills and retention (Day 27)

Manovia's database holds encrypted journal entries, mood notes, CBT records
and chat memory for people having a hard week. Losing it is a safety incident.
This page is the operating contract: how backups are taken, how a restore is
proven to work, and what is deleted on a schedule.

## What is backed up

Only PostgreSQL. Everything else is rebuildable: the API image comes from git,
the knowledge base is a curated corpus (`make ingest`), the FAISS index is
derived data, and logs/metrics are the observability platform's problem, not
the database's.

Field-level encryption means a stolen dump is not plaintext — every personal
text column is ciphertext under `FIELD_ENCRYPTION_KEY` (Day 3/17 envelope
scheme). That is defence in depth, **not** an excuse to treat dumps as public:
a dump is still sensitive data (row existence, timing, unencrypted metadata).
Store it where only the deploy platform can read it, and never commit one.

## How the nightly backup runs

Tool: `scripts/backup_db.sh` — `pg_dump --format=custom`, timestamped file,
`pg_restore --list` verification built in, local rotation with `--keep`.

```sh
DATABASE_URL="$PROD_DSN" ./scripts/backup_db.sh --output-dir /backups --keep 14
```

Schedule (03:00 UTC, low traffic): a small **Fly scheduled machine** running
the same container image with the command above (see `docs/deploy.md`), or
cron/systemd wherever the operator prefers. The DSN comes from the platform
secret store (`fly secrets set DATABASE_URL=...` on that machine — it is never
in the repo). Alternative/additional layer: enable the managed provider's own
daily snapshot if the Postgres add-on offers one — two independent mechanisms
beat one.

Retention: 14 local dumps by default, rotated by the script. The platform
volume holding `/backups` should have its own snapshot policy; keep at least
one dump from before every migration release.

## Restore drills — run monthly and after any schema change

A backup that has never been restored is a hope. The drill is one command:

```sh
./scripts/verify_backup_drill.sh \
  --source-url "$PROD_DSN" \
  --scratch-url "postgresql://.../manovia_drill"
```

It dumps the source with `backup_db.sh`, rebuilds a scratch database, restores
into it with `scripts/restore_db.sh`, and compares per-table row counts. It
prints `DRILL PASS: row counts match (N tables)` or a diff and exit 1.
`--scratch-url` (default: `manovia_drill` on the source's server) is destroyed
and recreated — point it at a scratch, never at anything real.

The same drill runs nightly in CI against a throwaway Postgres 16 service
container (`.github/workflows/backup.yml`), so the tooling cannot rot silently
between real drills. Recorded runs live in `docs/verification/`.

Restore steps for a real incident (RTO target: under an hour):

1. Stop writes: `fly machines stop <api-machine>` (or scale the app to 0) so
   the restored state is not immediately diverged from.
2. Provision a fresh Postgres (or use the existing one only if the failure was
   data corruption caught before writes resumed).
3. `scripts/restore_db.sh --backup <dump> --database-url "$NEW_DSN" --force`
4. Compare the printed row counts against the last known-good counts (the
   nightly drill's output is the reference).
5. Point the app at `NEW_DSN` (`fly secrets set DATABASE_URL=...`), start one
   machine, hit `/api/v1/ready`, then scale back up.
6. Post-incident note in `docs/verification/` — what was lost between the last
   dump and the failure (RPO is 24 h with nightly dumps; shorter if the
   operator schedules more).

## Retention (deletion) job

Privacy deletion is separate from backups: each person's retention window
(Settings → Privacy) is honoured by the Day-22 CLI, `scripts/apply_retention.py`
(`make retention`), which deletes personal data older than each user's own
window and prints a content-free report. Run it nightly, after the backup
completes (never before — the dump must cover what is about to be deleted):

```sh
# scheduled machine, after backup_db.sh:
DATABASE_URL="$PROD_DSN" python scripts/apply_retention.py --json >> /var/log/retention.jsonl
```

`DRY_RUN=1` first when changing anything. The job's own output carries counts
only — no content — so appending it to a log is safe.

## Secrets involved

- `DATABASE_URL` — the platform secret store on the backup machine.
- `FIELD_ENCRYPTION_KEY` — needed to *read* restored data, not to dump it.
  Its rotation is a runbook procedure (docs/runbook.md); a dump restored
  after rotation needs the keyring that covers the dump's era.

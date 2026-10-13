#!/bin/sh
# Manovia nightly Postgres backup (Day 27).
#
# Dumps the database in PostgreSQL's custom format (-Fc: compressed,
# pg_restore-able, supports selective restore) to a timestamped file.
#
# Usage:
#   DATABASE_URL=postgres://user:pass@host:5432/manovia ./scripts/backup_db.sh \
#       [--output-dir DIR] [--keep N]
#
# The connection comes from DATABASE_URL (postgres:// or postgresql://; the
# +asyncpg driver suffix is stripped automatically), or from the standard
# PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE variables if DATABASE_URL is unset.
#
# Secrets: the DSN comes from the environment (the platform secret store in
# production). It is never logged. `set +x` is deliberate.
#
# Scheduling is a deployment concern: on Fly this runs from a small scheduled
# machine (docs/backups.md), elsewhere from cron/systemd or the nightly GitHub
# Actions workflow (.github/workflows/backup.yml). Rotate/delete dumps on the
# same schedule; a dump of a wellbeing service is itself sensitive data —
# encrypt at rest (the platform volume) and never commit it anywhere.

set -eu

usage() {
    echo "usage: DATABASE_URL=... $0 [--output-dir DIR] [--keep N]" >&2
    exit 2
}

OUTPUT_DIR="${BACKUP_DIR:-./backups}"
KEEP="${BACKUP_KEEP:-14}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --output-dir)
            [ "$#" -ge 2 ] || usage
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --keep)
            [ "$#" -ge 2 ] || usage
            KEEP="$2"
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

# Resolve a DSN the libpq tools accept.
if [ -n "${DATABASE_URL:-}" ]; then
    DSN=$(printf '%s' "$DATABASE_URL" | sed 's|postgresql+asyncpg://|postgresql://|; s|postgresql+psycopg://|postgresql://|')
else
    DSN=""
fi

# pg_dump reads PG* variables itself when no connection string is given.
if [ -n "$DSN" ]; then
    DSN_ARG="$DSN"
else
    DSN_ARG=""
    : "${PGDATABASE:?set DATABASE_URL or PGDATABASE}"
fi

mkdir -p "$OUTPUT_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TARGET="$OUTPUT_DIR/manovia-$STAMP.dump"

# shellcheck disable=SC2086
if pg_dump --format=custom --no-owner --no-privileges $DSN_ARG --file="$TARGET"; then
    echo "backup written: $TARGET"
else
    echo "backup FAILED (exit $?); removing partial file" >&2
    rm -f "$TARGET"
    exit 1
fi

# A dump that cannot be listed is not a backup: verify before declaring success.
if pg_restore --list "$TARGET" >/dev/null 2>&1; then
    echo "backup verified (pg_restore --list ok): $TARGET"
else
    echo "backup verification FAILED for $TARGET" >&2
    exit 1
fi

# Local rotation: keep the newest KEEP dumps, delete older ones. This is a
# convenience for the host running the script, not a substitute for the
# platform's own retention on the storage that holds the copies.
count=0
for old in $(ls -1t "$OUTPUT_DIR"/manovia-*.dump 2>/dev/null); do
    count=$((count + 1))
    if [ "$count" -gt "$KEEP" ]; then
        rm -f "$old"
        echo "rotated (kept $KEEP): removed $old"
    fi
done

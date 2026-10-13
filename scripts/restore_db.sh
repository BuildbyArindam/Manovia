#!/bin/sh
# Manovia restore (Day 27) — the second half of every backup.
#
# Restores a `backup_db.sh` custom-format dump into a target database, then
# prints row counts for the key tables so a human can compare them against
# the source (the drill in docs/backups.md does exactly that).
#
# Usage:
#   ./scripts/restore_db.sh --backup backups/manovia-20261012T000000Z.dump \
#       [--database-url postgres://...target...] [--force]
#
# With --force it runs non-interactively (CI, the scripted drill); without it,
# it asks once. The restore is a FULL replace of the target database's schema
# and data (`pg_restore --clean --if-exists`): point it at a scratch or
# already-lost database, never at the live one on a hunch.
#
# Secrets: the DSN comes from the environment; it is never echoed.

set -eu

usage() {
    echo "usage: $0 --backup FILE.dump [--database-url postgres://...] [--force]" >&2
    exit 2
}

BACKUP=""
FORCE=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --backup)
            [ "$#" -ge 2 ] || usage
            BACKUP="$2"
            shift 2
            ;;
        --database-url)
            [ "$#" -ge 2 ] || usage
            DATABASE_URL="$2"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
            ;;
        *)
            usage
            ;;
    esac
done

[ -n "$BACKUP" ] || usage
[ -f "$BACKUP" ] || { echo "no such backup file: $BACKUP" >&2; exit 2; }

if [ -n "${DATABASE_URL:-}" ]; then
    DSN=$(printf '%s' "$DATABASE_URL" | sed 's|postgresql+asyncpg://|postgresql://|; s|postgresql+psycopg://|postgresql://|')
    DSN_ARG="$DSN"
else
    DSN_ARG=""
    : "${PGDATABASE:?set DATABASE_URL or PGDATABASE}"
fi

# The dump must be readable before anything touches the target.
pg_restore --list "$BACKUP" >/dev/null || { echo "not a readable dump: $BACKUP" >&2; exit 2; }

if [ "$FORCE" -ne 1 ]; then
    printf 'This REPLACES the target database (%s) with %s.\nType "restore" to continue: ' \
        "${PGDATABASE:-target}" "$BACKUP"
    read -r answer
    [ "$answer" = "restore" ] || { echo "aborted"; exit 1; }
fi

# --clean drops objects before recreating them; --if-exists tolerates an empty
# target; --no-owner/--no-privileges keep the restore working under the target
# role (ownership differs between environments by design). The target goes in
# --dbname: a bare positional argument to pg_restore is the INPUT FILE.
set +e
if [ -n "$DSN_ARG" ]; then
    pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$DSN_ARG" "$BACKUP"
else
    pg_restore --clean --if-exists --no-owner --no-privileges "$BACKUP"
fi
restore_status=$?
set -e
if [ "$restore_status" -eq 0 ]; then
    echo "restore completed from $BACKUP"
else
    echo "pg_restore reported errors (exit $restore_status); check the output above" >&2
    exit 1
fi

# Row counts for the tables that matter. Printed as TABLE=COUNT lines so the
# drill can diff them against the source counts. Data lives in encrypted
# columns; counts carry no content.
echo "--- row counts after restore ---"
psql ${DSN_ARG:-} --no-psqlrc --tuples-only --no-align <<'SQL' || {
SELECT format('SELECT %L || ''='' || count(*) FROM %I;', table_name, table_name)
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name \gexec
SQL
    echo "row-count query failed; verify manually with psql" >&2
    exit 1
}

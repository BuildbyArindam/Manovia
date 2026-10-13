#!/bin/sh
# The backup → restore → verify drill (Day 27).
#
# Proves the backup is restorable, end to end, against real PostgreSQL:
#
#   1. dump the source database with scripts/backup_db.sh,
#   2. restore that dump into a scratch database with scripts/restore_db.sh,
#   3. compare per-table row counts between source and scratch.
#
# Exit 0 and "DRILL PASS" only when every table's count matches.
#
# Usage:
#   ./scripts/verify_backup_drill.sh \
#       --source-url postgresql://user:pass@host:5432/manovia \
#       [--scratch-url postgresql://user:pass@host:5432/manovia_drill]
#
# The scratch database is DROPped and re-created — point --scratch-url at a
# scratch, never at anything real. Defaults assume the scratch lives on the
# same server as the source. Secrets come from the environment; never echoed.

set -eu

usage() {
    echo "usage: $0 --source-url postgresql://... [--scratch-url postgresql://...]" >&2
    exit 2
}

SOURCE_URL=""
SCRATCH_URL=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source-url)
            [ "$#" -ge 2 ] || usage
            SOURCE_URL="$2"
            shift 2
            ;;
        --scratch-url)
            [ "$#" -ge 2 ] || usage
            SCRATCH_URL="$2"
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

[ -n "$SOURCE_URL" ] || usage

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

# Split the source URL into "everything before the ?" and the query string, so
# path swaps work for both TCP and unix-socket DSNs (unix-socket DSNs carry
# ?host=/path/to/socket, which must survive every rewrite).
BASE_URL=${SOURCE_URL%%\?*}
QUERY=""
case "$SOURCE_URL" in
    *\?*) QUERY="?${SOURCE_URL#*\?}" ;;
esac

if [ -z "$SCRATCH_URL" ]; then
    SCRATCH_URL="${BASE_URL%/*}/manovia_drill${QUERY}"
fi
ADMIN_URL="${BASE_URL%/*}/postgres${QUERY}"
SCRATCH_DB="manovia_drill"

echo "== drill: dump the source =="
DATABASE_URL="$SOURCE_URL" "$SCRIPT_DIR/backup_db.sh" --output-dir "$WORK_DIR" --keep 1
DUMP=$(ls -1t "$WORK_DIR"/manovia-*.dump | head -1)
echo "dump: $DUMP"

echo "== drill: rebuild the scratch database =="
psql "$ADMIN_URL" --no-psqlrc --set ON_ERROR_STOP=1 \
    --command "DROP DATABASE IF EXISTS $SCRATCH_DB;" \
    --command "CREATE DATABASE $SCRATCH_DB;" >/dev/null
echo "scratch ready: $SCRATCH_DB"

echo "== drill: restore the dump into the scratch =="
"$SCRIPT_DIR/restore_db.sh" --backup "$DUMP" --database-url "$SCRATCH_URL" --force

echo "== drill: compare row counts (source vs scratch) =="
counts() {
    # Portable per-table counts (no XML functions: some Postgres builds ship
    # without libxml). psql's \gexec runs one generated count per table.
    psql "$1" --no-psqlrc --tuples-only --no-align <<'SQL'
SELECT format('SELECT %L || ''='' || count(*) FROM %I;', table_name, table_name)
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name \gexec
SQL
}
counts "$SOURCE_URL" > "$WORK_DIR/source.counts"
counts "$SCRATCH_URL" > "$WORK_DIR/scratch.counts"

table_count=$(wc -l < "$WORK_DIR/source.counts" | tr -d ' ')
if [ "$table_count" -eq 0 ]; then
    echo "DRILL FAIL: source reported no tables" >&2
    exit 1
fi

if diff -u "$WORK_DIR/source.counts" "$WORK_DIR/scratch.counts"; then
    echo "tables compared: $table_count"
    cat "$WORK_DIR/scratch.counts"
    echo "DRILL PASS: row counts match ($table_count tables)"
else
    echo "DRILL FAIL: row counts differ (see the diff above)" >&2
    exit 1
fi

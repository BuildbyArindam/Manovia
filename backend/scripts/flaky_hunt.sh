#!/usr/bin/env bash
# Day 26: run the whole backend suite N times in a row and report failures per
# run. A test that fails in some runs and passes in others is flaky; it is
# fixed or quarantined with an issue note in PROGRESS.md, never retried away.
#   scripts/flaky_hunt.sh [runs]        (default 5)
set -uo pipefail
cd "$(dirname "$0")/.."
RUNS="${1:-5}"; OUT="${FLAKY_OUT:-flaky-hunt}"; mkdir -p "$OUT"
status=0
for i in $(seq 1 "$RUNS"); do
  python -m pytest -q -p no:warnings -p no:cacheprovider > "$OUT/run-$i.log" 2>&1
  code=$?
  summary="$(tail -1 "$OUT/run-$i.log")"
  echo "run $i: exit=$code  $summary"
  grep -E "^(FAILED|ERROR) " "$OUT/run-$i.log" | sed 's/ - .*//' >> "$OUT/failures.txt" || true
  [ "$code" -eq 0 ] || status=1
done
if [ -s "$OUT/failures.txt" ]; then
  echo "--- tests that failed at least once (count = runs failed) ---"
  sort "$OUT/failures.txt" | uniq -c | sort -rn
fi
exit "$status"

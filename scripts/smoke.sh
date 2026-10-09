#!/usr/bin/env bash
# Manovia end-to-end smoke test (Day 7).
#
#   ./scripts/smoke.sh
#
# Starts the docker compose stack (unless SMOKE_SKIP_COMPOSE=1), waits for
# API readiness, creates a guest account, records the required consents, and
# checks the frontend returns HTTP 200. Exits non-zero on the first failure.
#
# Never prints tokens, payloads or user data (AGENTS.md rule 5).
#
# Overrides:
#   SMOKE_API_URL           default http://localhost:8000
#   SMOKE_WEB_URL           default http://localhost:3000
#   SMOKE_READY_TIMEOUT_S   default 120
#   SMOKE_SKIP_COMPOSE=1    do not run `docker compose up` (stack already up,
#                           e.g. a bare-metal uvicorn + vite behind a proxy)
#   SMOKE_COMPOSE           compose command, default "docker compose"

set -euo pipefail

API_URL="${SMOKE_API_URL:-http://localhost:8000}"
WEB_URL="${SMOKE_WEB_URL:-http://localhost:3000}"
READY_TIMEOUT_S="${SMOKE_READY_TIMEOUT_S:-120}"
COMPOSE="${SMOKE_COMPOSE:-docker compose}"

fail() {
    echo "SMOKE FAIL: $1" >&2
    exit 1
}

if [ "${SMOKE_SKIP_COMPOSE:-0}" != "1" ]; then
    echo "==> Starting services: $COMPOSE up -d --build"
    # shellcheck disable=SC2086 # SMOKE_COMPOSE may be several words.
    $COMPOSE up -d --build
fi

echo "==> Waiting for API readiness at ${API_URL}/api/v1/ready (timeout ${READY_TIMEOUT_S}s)"
deadline=$((SECONDS + READY_TIMEOUT_S))
while :; do
    status=$(curl -sS -o /dev/null -w '%{http_code}' "${API_URL}/api/v1/ready" || true)
    if [ "${status}" = "200" ]; then
        echo "    API ready (HTTP 200)"
        break
    fi
    if [ "${SECONDS}" -ge "${deadline}" ]; then
        fail "API never reported ready (last HTTP ${status:-none}; check 'docker compose logs api')"
    fi
    sleep 2
done

echo "==> Creating a guest account"
guest_response=$(curl -sS -w $'\n%{http_code}' -X POST "${API_URL}/api/v1/auth/guest" || true)
guest_status=$(tail -n 1 <<<"${guest_response}")
guest_body=$(sed '$d' <<<"${guest_response}")
[ "${guest_status}" = "201" ] || fail "POST /auth/guest returned HTTP ${guest_status}"
access_token=$(jq -r '.access_token // empty' <<<"${guest_body}")
[ -n "${access_token}" ] || fail "guest response had no access_token"

echo "==> Recording consents at the current document versions"
requirements=$(curl -sS "${API_URL}/api/v1/consent/requirements" || true)
[ -n "${requirements}" ] || fail "GET /consent/requirements returned nothing"
grants=$(jq -c '{grants: [.documents[] | {kind: .kind, version: .version, granted: true}]}' <<<"${requirements}")
[ "$(jq -r '.grants | length' <<<"${grants}")" -gt 0 ] || fail "no consent documents returned"
consent_status=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "${API_URL}/api/v1/consent" \
    -H "Authorization: Bearer ${access_token}" \
    -H 'Content-Type: application/json' \
    -d "${grants}" || true)
[ "${consent_status}" = "201" ] || fail "POST /consent returned HTTP ${consent_status}"

echo "==> Checking the frontend at ${WEB_URL}/"
web_status=$(curl -sS -o /dev/null -w '%{http_code}' "${WEB_URL}/" || true)
[ "${web_status}" = "200" ] || fail "frontend returned HTTP ${web_status}"

echo "SMOKE PASS: API ready, guest created, consent recorded, frontend HTTP 200"

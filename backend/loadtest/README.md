# Load test (Day 26)

A Locust scenario that mixes mood check-ins (the non-LLM path) with chat
messages (Fake LLM, so latency measures Manovia rather than a vendor).

```bash
cd backend
pip install -e ".[dev]" && pip install -r loadtest/requirements.txt
loadtest/run.sh 100 20 2m      # 100 users, 20 new users/second, 2 minutes
```

`run.sh` starts the API on a throwaway SQLite file with `LLM_PROVIDER=fake` and
rate limiting off, waits for `/api/v1/ready`, runs Locust headless, and stops the
API. Outputs land in `loadtest/out/` (git-ignored): CSVs and `server.log`.

Point `--host` at a staging deployment to measure the deployed shape (workers x
Postgres) instead; do that before quoting any p95 for production.

## What was measured (2026-10-24, this sandbox: 2 CPUs, SQLite, one uvicorn worker)

100 simulated people, 2 minutes, 8,913 requests, 0 failures in this run.

| Endpoint | Requests | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|
| `POST /mood` (non-LLM) | 4,283 | 91 | 360 | 660 |
| `GET /mood` (non-LLM) | 1,714 | 63 | 240 | 400 |
| `GET /mood/streak` (non-LLM) | 1,723 | 57 | 240 | 400 |
| `POST /chat/.../messages` (Fake LLM) | 893 | 97 | 440 | 810 |
| `POST /auth/guest` (once per person) | 100 | 320 | 1,100 | 3,000 |
| `POST /consent` (once per person) | 100 | 290 | 1,000 | 1,300 |
| `POST /chat/sessions` (once per person) | 100 | 210 | 620 | 920 |

The target (p95 < 500 ms for non-LLM endpoints) is met for every steady-state
endpoint. The sign-up requests are slow only during the first seconds, while 100
people arrive together on one worker.

An earlier run of the same scenario recorded 2 HTTP 500s on `auth/guest` out of
8,659 requests (0.02 %). Two re-runs did not reproduce them and the server log of
the first run was not captured, so the cause is **unknown**; `run.sh` now keeps
`server.log` so the next occurrence can be diagnosed. Treat it as an open item,
not a closed one.

## Production shape: gunicorn + Postgres 16 (2026-10-24, same 2-CPU sandbox)

The same scenario against the container's real process model (`gunicorn -c
gunicorn.conf.py`, one worker as configured, `APP_ENV=production`) on a local
PostgreSQL 16 server instead of SQLite. 9,587 requests, **0 failures**.

| Endpoint | Requests | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|
| `POST /mood` (non-LLM) | 4,673 | 10 | 34 | 140 |
| `GET /mood` (non-LLM) | 1,794 | 6 | 19 | 83 |
| `GET /mood/streak` (non-LLM) | 1,880 | 6 | 18 | 95 |
| `POST /chat/.../messages` (Fake LLM) | 874 | 16 | 48 | 150 |
| `POST /auth/guest` | 100 | 88 | 170 | 180 |
| `POST /consent` | 100 | 97 | 130 | 150 |
| `POST /chat/sessions` | 100 | 85 | 130 | 180 |

The SQLite figures above are the pessimistic case: SQLite serialises writers, and
with Postgres the same load is far faster at p95. This is still not a claim about
a hosted deployment (network latency, a managed database, TLS and real traffic
shape are all missing), and Locust shared the two CPUs with the server.

The same session also fired 12 simultaneous refreshes of one token (exactly 1
succeeded, 11 got `refresh_token_reused`) and 10 simultaneous identical
questionnaire submissions (one stored result), against real Postgres.

## What this does not show

- Nothing here is the deployed shape: no gunicorn workers, no Postgres, no TLS
  proxy. SQLite serialises writers, which flatters reads and punishes bursts.
- The Fake LLM returns instantly. Real provider latency is additional.
- Locust runs on the same two CPUs as the API, so both compete for them.

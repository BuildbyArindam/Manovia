# Manovia production deployment

The map: **API + Postgres on Fly.io** (ADR
[0026](adr/0026-deployment-platform.md)), **web on Netlify**, secrets only via
platform secret stores, migrations as a release step (never on boot). Every
command below is the real one; steps needing platform accounts are marked
**[needs account]** — they cannot be run from the dev sandbox and were
verified as far as the sandbox allows (see `docs/verification/day27-*`).

## 0. Prerequisites [needs account]

- A Fly.io account and `flyctl` (`brew install flyctl` or
  <https://fly.io/docs/flyctl/install/>), then `fly auth login`.
- A Netlify account with the Netlify CLI (`npm i -g netlify-cli`), then
  `netlify login`.
- A Sentry project (optional but recommended): its DSN becomes a secret.

## 1. The API on Fly.io

### 1.1 Create the app and the database

```sh
fly launch --no-deploy --copy-config        # picks up fly.toml (app name, region bom)
fly postgres create --name manovia-db --region bom --initial-cluster-size 1
fly postgres attach manovia-db              # sets DATABASE_URL as a secret
```

`fly postgres attach` writes `DATABASE_URL` into the app's secret store. The
DSN arrives as `postgres://...`; the app accepts it (asyncpg driver is chosen
automatically — see `.env.example`).

### 1.2 Set the required secrets

Production fails fast without these (`Settings` validators — an app missing
one refuses to boot; verified in `docs/verification/day27-*`):

```sh
fly secrets set APP_ENV=production
fly secrets set SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
fly secrets set FIELD_ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
# Optional:
fly secrets set SENTRY_DSN="https://...@o..ingest.sentry.io/.."
fly secrets set ALLOWED_ORIGINS="https://manovia.netlify.app,https://manovia.app"
```

`ALLOWED_ORIGINS` must list the exact Netlify origins (scheme + host, no
trailing slash) — it drives both CORS and the origin guard. Add a custom
domain later with `fly certs add app.manovia.app` and the DNS records `fly
certs` prints; then add that origin to `ALLOWED_ORIGINS` too.

### 1.3 First deploy

```sh
fly deploy          # builds backend/Dockerfile, runs the release_command, then ships
```

`fly.toml` sets `[deploy] release_command = "alembic upgrade head"`: Fly runs
a one-off migration machine with the same image and the production
environment **before** the new VMs take traffic, exactly like the local
`make prod-up` rehearsal (`docker-compose.prod.yml`). On success the release
completes; on failure the deploy stops. Production containers never migrate
on boot (`RUN_MIGRATIONS=false`).

### 1.4 Verify

```sh
fly status
curl -s https://<app>.fly.dev/api/v1/health          # {"status":"ok"}
curl -s https://<app>.fly.dev/api/v1/ready           # {"status":"ready",...}
```

## 2. The web on Netlify

```sh
netlify init        # link (or create) the site; build settings come from netlify.toml
netlify env set VITE_API_BASE_URL "/api/v1"    # same-origin; the /api rewrite proxies
netlify deploy --prod
```

`netlify.toml` (committed) carries the build command, the SPA redirect, the
`/api/* → https://<app>.fly.dev/api/*` same-origin rewrite, and the strict
security headers copied from `docker/security-headers.conf`.

**[needs account]** The Netlify rewrite target must be the real Fly hostname
after step 1.4 — edit the `to` line in `netlify.toml` in the same change.

Custom domain (optional): `netlify domains:add manovia.app` and follow the
DNS instructions; then add `https://manovia.app` to `ALLOWED_ORIGINS` on Fly.

## 3. Nightly backup and retention machines

```sh
fly machine run . --schedule=hourly=3h \   # or the region's scheduled slot
  --command-fast=false ...
```

Concretely (see docs/backups.md for the contract):

- **backup** (03:00 UTC): a scheduled machine running the API image with the
  command `sh scripts/backup_db.sh --output-dir /backups --keep 14`, with
  `DATABASE_URL` already a secret, and a volume mounted at `/backups`
  (`fly volumes create backups --size 3`). Schedule with
  `fly machine update --schedule` or create it once with
  `fly machine run --schedule=daily@03:00`.
- **retention** (03:30 UTC): same pattern, command
  `python scripts/apply_retention.py --json`, after the backup so the dump
  always covers what is about to be deleted.

Scheduled machine syntax changes occasionally; `fly machine run --help` is
the authority. What must not change: the jobs read their DSN from the secret
store, and the backup drill (`scripts/verify_backup_drill.sh`) runs monthly —
see docs/backups.md.

## 4. Uptime check

- Fly's own checks poll `/api/v1/health` every 30 s (fly.toml) and restart
  unhealthy machines.
- Point an external uptime monitor (Better Stack, UptimeRobot, or GitHub
  Actions cron) at `https://<app>.fly.dev/api/v1/ready` — it checks the
  database too — and at `https://<site>.netlify.app/` expecting HTTP 200.
  The runbook's first step on any alert is `fly status` + the runbook's
  outage pages.

## 5. Releases

Tagged versions (`v*`) build and publish both container images to GHCR and
cut a GitHub release with generated notes:
`.github/workflows/release.yml`. Its migration job proves
`alembic upgrade head` (and a downgrade/upgrade round-trip) against a fresh
Postgres 16 **before** images are published.

## 6. The demo label (required on every deployment)

Every deployment of this project is a **portfolio demo**, and the app says so on
screen, not only in the repository. The string lives in
`frontend/src/lib/i18n/locales/{en,hi,bn}.json` under `app.footerPortfolio`
(*"Portfolio demo — not a clinical service. Clinical review is not complete."*)
and is rendered in the app shell footer on every route, including the public
share viewer, so it is compiled into the bundle rather than fetched from a config
service that could be down.

Check it after a deploy (no browser needed):

```sh
cd frontend && npm run build
grep -c "Portfolio demo" dist/assets/*.js        # ≥ 1 (English is in the entry chunk)
grep -c "पोर्टफ़ोलियो डेमो" dist/assets/hi-*.js   # Hindi lazy chunk
grep -c "পোর্টফোলিও ডেমো" dist/assets/bn-*.js     # Bengali lazy chunk
```

If a deployment ever needs a different label (for example a hosted demo under a
different name), change the three locale files — **not** the component — and
update the test in `frontend/src/App.test.tsx` that asserts the footer text.

## 7. What is deliberately NOT automated here

- The first `fly launch` / `netlify init` (account-bound, interactive).
- Custom domain DNS.
- `fly secrets` values — never stored in the repo; `.env.example` documents
  every name the app reads.

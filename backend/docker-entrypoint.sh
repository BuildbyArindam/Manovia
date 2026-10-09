#!/bin/sh
# Manovia API container entrypoint.
#
# Dev convenience only: when APP_ENV is "development" (the default), the
# container brings the database to `alembic upgrade head` before starting
# uvicorn, so a fresh `make up` lands on a migrated database. Production must
# never auto-migrate on boot — there migrations are a deliberate, observable
# deploy step (run the same image with a one-shot `alembic upgrade head`
# command), so anything except APP_ENV=development skips this entirely.
# RUN_MIGRATIONS=false opts out even in development.

set -eu

APP_ENV="${APP_ENV:-development}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"

if [ "$APP_ENV" = "development" ] && [ "$RUN_MIGRATIONS" = "true" ]; then
    echo "[entrypoint] APP_ENV=development — running 'alembic upgrade head'"
    alembic upgrade head
else
    echo "[entrypoint] skipping migrations (APP_ENV=$APP_ENV, RUN_MIGRATIONS=$RUN_MIGRATIONS)"
fi

exec "$@"

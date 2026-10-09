# syntax=docker/dockerfile:1
# Manovia API image. Multi-stage: dependencies are installed into a venv in a
# builder stage, and the runtime stage carries the venv plus only the files the
# app and its migrations need — no compiler, no test suite, no dev extras.
#
# The HF emotion stack (the `nlp` extra: torch + transformers) is deliberately
# NOT installed: it would add several GB to the image. The analyzer chain then
# answers with the lexicon fallbacks (its designed degradation path, Day 6).
# Run the container with EMOTION_ANALYZER=keyword to skip even the attempt.

# ---------------------------------------------------------------------------
# Builder: resolve and install the runtime dependencies once.
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install the package (non-editable) into the venv. The BuildKit cache mount
# keeps downloaded wheels across rebuilds so source-only edits stay fast.
COPY backend/pyproject.toml backend/README.md ./
COPY backend/app ./app
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install .

# ---------------------------------------------------------------------------
# Runtime: slim image, non-root user, healthcheck, dev-only auto-migrations.
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --system app && useradd --system --gid app --no-create-home app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv/backend

# App source (also makes the package importable from the workdir), alembic for
# the entrypoint's dev migrations, and the entrypoint itself.
COPY --chown=app:app backend/app ./app
COPY --chown=app:app backend/alembic ./alembic
COPY --chown=app:app backend/alembic.ini ./
COPY --chmod=555 backend/docker-entrypoint.sh /usr/local/bin/manovia-entrypoint

USER app

EXPOSE 8000

# Liveness, not readiness: /api/v1/ready depends on the database and would flap
# the container when postgres restarts; orchestrators should probe /ready
# separately. python is the only HTTP client guaranteed to exist in the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"]

# The entrypoint runs `alembic upgrade head` when APP_ENV=development
# (see backend/docker-entrypoint.sh — dev containers only), then execs CMD.
ENTRYPOINT ["manovia-entrypoint"]
# --no-access-log: uvicorn's access log prints the full request line including
# query strings, which may carry user text (AGENTS.md rule 5). The app's own
# path-only structlog request line comes from RequestIDMiddleware.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

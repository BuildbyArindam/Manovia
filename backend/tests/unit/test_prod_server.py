"""Day 27: the production server contract (Gunicorn + Uvicorn workers).

``gunicorn.conf.py`` is a deployment decision in code, so it gets the same
treatment as any other load-bearing configuration: tests that fail loudly when
someone quietly changes the process model — re-enables the access log, forks
after building the async engine, or lets a graceful-shutdown window grow past
the heartbeat timeout.
"""

from __future__ import annotations

import importlib.util
import pathlib
from typing import Any

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_config() -> Any:
    """Import ``backend/gunicorn.conf.py`` by path.

    Loaded by path, not by module name: the filename was chosen for the
    ``gunicorn -c`` flag, not for the import system, and must not collide with
    any real package.
    """
    path = BACKEND_ROOT / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location("manovia_gunicorn_conf", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def conf(clean_env: None) -> Any:
    """The config with defaults (no GUNICORN_* / PORT env in the way)."""
    return _load_config()


def test_workers_run_uvicorn_via_gunicorn(conf: Any) -> None:
    """The process model is Gunicorn managing Uvicorn workers."""
    assert conf.worker_class == "manovia_worker.AccessLogFreeUvicornWorker"
    assert int(conf.workers) == 1  # see docstring: ephemeral sessions are per process
    assert conf.bind.startswith("0.0.0.0:")


def test_preload_stays_off(conf: Any) -> None:
    """create_app() builds the async engine at import time; forking after
    that would share one connection pool across workers."""
    assert conf.preload_app is False


def test_graceful_shutdown_window_is_bounded(conf: Any) -> None:
    """SIGTERM drains in-flight requests; the drain window must exist and sit
    inside the worker heartbeat timeout so a stuck worker cannot linger."""
    assert int(conf.graceful_timeout) >= 1
    assert int(conf.timeout) > int(conf.graceful_timeout)


def test_http_access_log_is_off(conf: Any) -> None:
    """The access log prints query strings, which may carry user text
    (AGENTS.md rule 5). The app's structlog request line covers observability."""
    assert conf.accesslog is None


def test_env_overrides_are_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sizing knobs are deployment decisions: the env must win over defaults."""
    monkeypatch.setenv("GUNICORN_WORKERS", "4")
    monkeypatch.setenv("GUNICORN_GRACEFUL_TIMEOUT", "10")
    monkeypatch.setenv("PORT", "9001")
    conf = _load_config()
    assert int(conf.workers) == 4
    assert int(conf.graceful_timeout) == 10
    assert conf.bind == "0.0.0.0:9001"


def test_worker_class_disables_access_log_and_server_header() -> None:
    """The worker subclass keeps uvicorn's own access log off, not just
    gunicorn's — uvicorn formats the request target (with query string)."""
    path = BACKEND_ROOT / "manovia_worker.py"
    spec = importlib.util.spec_from_file_location("manovia_worker", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from uvicorn_worker import UvicornWorker

    assert issubclass(module.AccessLogFreeUvicornWorker, UvicornWorker)
    kwargs = module.AccessLogFreeUvicornWorker.CONFIG_KWARGS
    assert kwargs["access_log"] is False
    assert kwargs["server_header"] is False


def test_dockerfile_runs_the_production_server() -> None:
    """The image CMD runs gunicorn against the reviewed config file, and the
    config + worker modules are actually copied into the image."""
    dockerfile = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")
    cmd = next(line for line in dockerfile.splitlines() if line.startswith("CMD "))
    assert '"gunicorn"' in cmd
    assert '"-c"' in cmd
    assert '"gunicorn.conf.py"' in cmd
    assert '"app.main:app"' in cmd
    assert "backend/gunicorn.conf.py ./" in dockerfile
    assert "backend/manovia_worker.py ./" in dockerfile
    # The single-process CMD must be gone: two CMDs would silently keep the last.
    assert dockerfile.count("\nCMD ") == 1


def test_production_compose_overlay_keeps_the_release_contract() -> None:
    """The prod overlay runs APP_ENV=production, never auto-migrates, and
    migrates as a one-shot service the API waits on."""
    overlay = (BACKEND_ROOT.parent / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "APP_ENV: production" in overlay
    assert 'RUN_MIGRATIONS: "false"' in overlay
    assert 'command: ["alembic", "upgrade", "head"]' in overlay
    assert "service_completed_successfully" in overlay
    # Production posture: no placeholder secrets — compose must refuse to
    # start without the real values.
    assert overlay.count("${SECRET_KEY:?") == 1 or "${SECRET_KEY:?" in overlay
    assert "${FIELD_ENCRYPTION_KEY:?" in overlay

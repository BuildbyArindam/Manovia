"""Every ``from app.… import X`` in the repository must resolve.

Why this test exists
--------------------
The Day 11 merge landed a superseded draft alongside the shipped code. The draft
added providers to :mod:`app.api.deps` that imported ``EphemeralStore`` from
:mod:`app.services.chat.ephemeral` — a name that does not exist (the class is
``EphemeralSessionStore``) — and called a ``ChatOrchestrator`` constructor that
was never the shipped one. ``app.api.deps`` is imported by every route module,
so ``conftest`` failed at collection and **the whole suite reported zero tests**
while the code that shipped was fine and the code that was pasted over it was
not.

Lint cannot catch this class of drift: ruff and mypy see a name that "might"
exist, and a merge that leaves two definitions of the same symbol in one file
silently keeps the last one (that is how ``app/core/config.py`` ended up with two
``_check_chat_settings`` validators and ``app/api/v1/chat.py`` with two
``CreateSessionIn`` models, the second shadowing the first).

What this test adds over "pytest fails to collect anyway":

* a **named** failure — ``X imports Y from Z, which does not define it`` — instead
  of a stack of ``ImportError``s pointing at whichever module imported first;
* coverage of ``app/`` modules that no test imports at all, where nothing else
  would ever notice (a dead provider module can rot for a whole milestone);
* a single place that says "the import graph of this package is intact", so a
  reviewer does not have to infer it from a green suite.

The check is structural, not exhaustive type checking: for every import
statement that pulls a name out of an ``app`` module, the target module is
imported and the name is looked up on it.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
#: Both trees: the drift that motivated this test lived in `app/`, and its stale
#: test twin lived in `tests/`. Neither may import a name that has gone away.
SCANNED_ROOTS = ("app", "tests")


def _iter_python_files() -> Iterator[Path]:
    for root in SCANNED_ROOTS:
        base = BACKEND_ROOT / root
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def _module_name(path: Path) -> str:
    relative = path.relative_to(BACKEND_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_target(node: ast.ImportFrom, package: str) -> str | None:
    """The absolute module an ``ImportFrom`` node pulls names from."""
    if node.level == 0:
        return node.module
    # Relative import: walk up `level - 1` from the current package.
    parts = package.split(".")
    if node.level > len(parts):
        return None
    base = parts[: len(parts) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _import_targets() -> Iterator[tuple[str, str, tuple[str, ...]]]:
    """``(importing module, imported module, names)`` for every app-internal import."""
    for path in _iter_python_files():
        package = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _resolve_target(node, package)
            if target is None or not target.startswith("app."):
                continue
            names = tuple(alias.name for alias in node.names if alias.name != "*")
            if names:
                yield package, target, names


#: Materialised once at import time so the parametrisation is stable and the
#: collection itself is cheap.
TARGETS: tuple[tuple[str, str, tuple[str, ...]], ...] = tuple(_import_targets())


def test_the_scan_actually_finds_imports() -> None:
    """Guard against the guard silently scanning nothing (a moved directory)."""
    assert len(TARGETS) > 200, f"only {len(TARGETS)} app-internal imports found — scan broke?"
    modules = {target for _, target, _ in TARGETS}
    assert "app.services.chat.ephemeral" in modules
    assert "app.api.deps" in {package for package, _, _ in TARGETS}


@pytest.mark.parametrize(
    ("package", "target", "names"),
    TARGETS,
    ids=[f"{package}->{target}" for package, target, _ in TARGETS],
)
def test_every_imported_name_exists(package: str, target: str, names: tuple[str, ...]) -> None:
    """`package` imports `names` from `target`; each must really be there."""
    module = importlib.import_module(target)
    for name in names:
        assert hasattr(module, name), (
            f"{package} imports `{name}` from {target}, which does not define it. "
            f"A rename or a stale merge left this behind; the app cannot start."
        )


def test_every_app_module_imports_cleanly() -> None:
    """Importing the package tree must not raise (the failure that hid in Day 11)."""
    import app

    failures: list[str] = []
    for info in pkgutil.walk_packages(app.__path__, prefix="app."):
        if info.name.endswith(".cli"):  # pragma: no cover - no such module today
            continue
        try:
            importlib.import_module(info.name)
        except Exception as exc:  # the point is to report any of them
            failures.append(f"{info.name}: {type(exc).__name__}: {exc}")
    assert failures == [], "modules that failed to import:\n" + "\n".join(failures)

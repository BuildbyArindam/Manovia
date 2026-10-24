"""Day 26: mutation testing for the safety decision code.

Coverage says a line *ran*; mutation testing says a test would *notice* if the
line were wrong. This script makes one small, deliberate mistake at a time in a
safety module (flip a comparison, swap and/or, drop a ``not``, nudge a number,
turn ``max`` into ``min``), runs the tests that are meant to guard that module,
and records whether any of them failed. A mutant that no test notices
("survived") is a gap in the safety suite, not in the code under test.

Why not mutmut: its copy-the-tree layout breaks the safety suite, which reads
sibling directories (``ml/``, ``evals/``) by relative path. This runner mutates
the real file in place and always restores it (also on Ctrl-C), so it must not
run while anything else is editing the same files.

    python scripts/mutation_safety.py                 # every default target
    python scripts/mutation_safety.py --target ensemble --limit 20
    python scripts/mutation_safety.py --report ../docs/mutation-report.md

Exit status is 0 even when mutants survive: this is a report, not a gate (the
surviving list is reviewed by a person; see docs/adr/0049).
"""

from __future__ import annotations

import argparse
import ast
import copy
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

TARGETS: dict[str, tuple[str, list[str]]] = {
    "ensemble": (
        "app/services/safety/ensemble.py",
        [
            "tests/safety/test_ensemble.py",
            "tests/safety/test_ensemble_boundaries.py",
            "tests/safety/test_trained_classifier.py",
        ],
    ),
    "escalation": (
        "app/services/safety/escalation.py",
        ["tests/safety/test_escalation.py", "tests/safety/test_escalation_session_boundaries.py"],
    ),
    "rules": (
        "app/services/safety/rules.py",
        [
            "tests/safety/test_rules_engine.py",
            "tests/safety/test_loader_and_engine_branches.py",
            "tests/safety/test_rules_windowing.py",
        ],
    ),
    "session_risk": (
        "app/services/safety/session_risk.py",
        [
            "tests/safety/test_session_risk.py",
            "tests/safety/test_escalation_session_boundaries.py",
            "tests/redteam",
        ],
    ),
}

_CMP_SWAP: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}


@dataclass(frozen=True)
class Mutant:
    index: int
    line: int
    description: str


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _sites(tree: ast.AST) -> list[tuple[ast.AST, str]]:
    """Every mutable site, in a stable order, with a human description."""
    docs = _docstring_nodes(tree)
    sites: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Compare):
            for position, op in enumerate(node.ops):
                if type(op) in _CMP_SWAP:
                    sites.append(
                        (
                            node,
                            f"L{line}: {type(op).__name__} -> "
                            f"{_CMP_SWAP[type(op)].__name__}#{position}",
                        )
                    )
        elif isinstance(node, ast.BoolOp):
            other = "Or" if isinstance(node.op, ast.And) else "And"
            sites.append((node, f"L{line}: {type(node.op).__name__} -> {other}"))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            sites.append((node, f"L{line}: drop `not`"))
        elif isinstance(node, ast.Constant) and id(node) not in docs:
            if isinstance(node.value, bool):
                sites.append((node, f"L{line}: {node.value} -> {not node.value}"))
            elif isinstance(node.value, int) and not isinstance(node.value, bool):
                sites.append((node, f"L{line}: {node.value} -> {node.value + 1}"))
            elif isinstance(node.value, float):
                sites.append((node, f"L{line}: {node.value} -> {node.value + 0.1:.3g}"))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"max", "min"}
        ):
            swap = "min" if node.func.id == "max" else "max"
            sites.append((node, f"L{line}: {node.func.id}() -> {swap}()"))
    return sites


def _apply(tree: ast.Module, site_index: int) -> ast.Module:
    clone = copy.deepcopy(tree)
    node = _sites(clone)[site_index][0]
    if isinstance(node, ast.Compare):
        for position, op in enumerate(node.ops):
            if type(op) in _CMP_SWAP:
                node.ops[position] = _CMP_SWAP[type(op)]()
                break
    elif isinstance(node, ast.BoolOp):
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif isinstance(node, ast.UnaryOp):
        node.op = ast.UAdd()  # `not x` -> `+x`: truthiness is preserved, negation is gone
        node.operand = ast.UnaryOp(
            op=ast.Not(), operand=ast.UnaryOp(op=ast.Not(), operand=node.operand)
        )
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            node.value = not node.value
        elif isinstance(node.value, int):
            node.value += 1
        elif isinstance(node.value, float):
            node.value += 0.1
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        node.func.id = "min" if node.func.id == "max" else "max"
    ast.fix_missing_locations(clone)
    return clone


def _run_tests(tests: list[str], timeout: int) -> bool:
    """True when the tests FAIL (the mutant is killed)."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(BACKEND))
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-x",
                "-q",
                "-p",
                "no:warnings",
                "-p",
                "no:cacheprovider",
                *tests,
            ],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return True  # a mutant that hangs is noticed by a timeout, which is a kill
    return result.returncode != 0


def _purge_pyc(path: Path) -> None:
    cache = path.parent / "__pycache__"
    if cache.is_dir():
        for pyc in cache.glob(f"{path.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", choices=sorted(TARGETS), action="append")
    parser.add_argument(
        "--limit", type=int, default=0, help="at most N mutants per target (0 = all)"
    )
    parser.add_argument("--timeout", type=int, default=120, help="seconds per mutant")
    parser.add_argument("--report", type=Path, help="write a Markdown report here")
    args = parser.parse_args()

    names = args.target or [t for t in sorted(TARGETS) if t != "rules"]
    sections: list[str] = []
    totals = {"killed": 0, "survived": 0}
    started = time.time()
    for name in names:
        rel, tests = TARGETS[name]
        path = BACKEND / rel
        original = path.read_text(encoding="utf-8")
        tree = ast.parse(original)
        total_sites = len(_sites(tree))
        indexes = list(range(total_sites))
        if args.limit:
            step = max(1, total_sites // args.limit)
            indexes = indexes[::step][: args.limit]
        # Baseline: the guarding tests must be green before any mutant is judged.
        if _run_tests(tests, args.timeout):
            print(f"[{name}] baseline tests FAIL; fix them before mutating", file=sys.stderr)
            return 2
        killed: list[Mutant] = []
        survived: list[Mutant] = []
        try:
            for done, site in enumerate(indexes, start=1):
                description = _sites(tree)[site][1]
                line = int(description.split(":")[0][1:])
                mutant = Mutant(site, line, description)
                path.write_text(ast.unparse(_apply(tree, site)) + "\n", encoding="utf-8")
                _purge_pyc(path)
                (killed if _run_tests(tests, args.timeout) else survived).append(mutant)
                print(
                    f"[{name}] {done}/{len(indexes)} {description} -> "
                    f"{'killed' if mutant in killed else 'SURVIVED'}",
                    flush=True,
                )
        finally:
            path.write_text(original, encoding="utf-8")
            _purge_pyc(path)
        totals["killed"] += len(killed)
        totals["survived"] += len(survived)
        score = 100 * len(killed) / max(1, len(killed) + len(survived))
        section = [
            f"## `{rel}`",
            "",
            f"Guarding tests: `{' '.join(tests)}`",
            "",
            f"Mutants tried: {len(killed) + len(survived)} of {total_sites} sites. "
            f"Killed {len(killed)}, survived {len(survived)} (**{score:.1f}%** mutation score).",
            "",
        ]
        if survived:
            section += ["| Surviving mutant | Triage |", "|---|---|"]
            section += [f"| {m.description} | _to review_ |" for m in survived]
        sections.append("\n".join(section))
    summary = (
        f"Total: killed {totals['killed']}, survived {totals['survived']} "
        f"in {time.time() - started:.0f}s."
    )
    print(summary)
    if args.report:
        args.report.write_text(
            "# Safety mutation report\n\nGenerated by `backend/scripts/mutation_safety.py`.\n\n"
            + summary
            + "\n\n"
            + "\n\n".join(sections)
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

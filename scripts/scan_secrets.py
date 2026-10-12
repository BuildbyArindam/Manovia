#!/usr/bin/env python3
"""An offline stand-in for the CI gitleaks job — same rules, different engine.

CI runs the real thing (`gitleaks/gitleaks-action`), and that remains the
authority. This script exists because the binary cannot always be reached (the
sandbox this was written in allows PyPI and GitHub source downloads but not
GitHub *release assets*), and "the scan is in CI, trust us" is not evidence.

What it does: it takes gitleaks' own default rule set from a tagged release of
the gitleaks source, evaluates those regexes with Python's `re`, and reports
matches over the working tree or over a commit range. Where a rule is skipped
because Python's regex dialect cannot express it, the skip is *printed* rather
than silently dropped.

What it does not do — the honest gap, also printed at the end of every run:

* no baseline handling and no git-remote allowlists;
* `entropy`, `secretGroup`, `path`, per-rule allowlists (including
  `condition` and `regexTarget`) *are* implemented — entropy with gitleaks'
  own formula, ported from `detect/utils.go` — but a divergence between
  Python `re` and Go RE2 is possible in principle (RE2 lacks lookarounds, so
  the direction of error is "Python matches more");
* `[[allowlists]]` rules keyed to a *regex* `targetRule` are not evaluated
* binary and non-UTF-8 files are skipped, not decoded;
* it scans *tracked* files (and, with `--history`, blobs from `git rev-list`),
  never `.git` internals or files excluded by `.gitignore`.

So: a clean run here is a strong signal, not proof. A hit here must be triaged
either way, and the CI job is what gates the merge.

The rule file itself is `scripts/gitleaks-config.toml`, kept **byte-identical**
to upstream's `config/gitleaks.toml` at tag v8.28.0:

    sha256 c3bee40d87bb4689e3f5ffa5cc0315f7e6e4d43f4e9388397fca59f29407b432

so a reviewer can diff it against the release instead of trusting a copy. It
looks alarming on a skim, because it contains strings shaped like Google API
keys — those are the `gcp-api-key` rule's own allowlist examples (see the
`[[rules.allowlists]]` table under that rule), which is why neither this script
nor gitleaks reports them. If the file ever needs editing, edit rules in a
separate config and pass `--config`; do not fork this one.

Usage:
    python scripts/scan_secrets.py                       # working tree
    python scripts/scan_secrets.py --history v8.28.0..HEAD
    python scripts/scan_secrets.py --config /path/to/gitleaks.toml

Exit code 1 if anything is reported, 0 if clean, 2 on a usage/setup error.
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_BYTES = 2_000_000


@dataclass
class AllowlistEntry:
    """One `[[rules.allowlists]]` table, compiled.

    gitleaks allowlists are where nearly all of its precision lives: the
    `generic-api-key` rule alone ships four of them, mostly `regexTarget =
    "line"` keyword lists that reject ordinary prose. Ignoring them (the first
    version of this script did) turns the scanner into a noisy grep — useful to
    nobody, because a wall of false positives trains people to ignore it.
    """

    regexes: tuple[re.Pattern[str], ...] = ()
    paths: tuple[re.Pattern[str], ...] = ()
    stopwords: frozenset[str] = frozenset()
    condition: str = "OR"
    regex_target: str = "secret"


@dataclass
class Rule:
    """One gitleaks rule, compiled for Python."""

    id: str
    description: str
    # A few rules select on the file *path* alone (e.g. a `.p12` bundle), so
    # the regex is optional and the path filter is the whole rule.
    pattern: re.Pattern[str] | None = None
    secret_group: int = 0
    keywords: tuple[str, ...] = ()
    path_pattern: re.Pattern[str] | None = None
    allowlists: tuple[AllowlistEntry, ...] = ()
    entropy: float | None = None


@dataclass
class Config:
    rules: list[Rule] = field(default_factory=list)
    allowlist_paths: list[re.Pattern[str]] = field(default_factory=list)
    allowlist_regexes: list[re.Pattern[str]] = field(default_factory=list)
    allowlist_stopwords: set[str] = field(default_factory=set)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _transliterate(pattern: str) -> str:
    """Translate the two Go/RE2 idioms Python's `re` cannot express directly.

    * ``\z`` (absolute end of text) — Python spells it ``\Z``.
    * mid-pattern ``(?i)`` / ``(?s)`` / ``(?m)`` — RE2 scopes the flag from that
      point on; Python only accepts inline flags at the start of the
      expression. Hoisting the flag to the front widens it to the whole
      pattern. That is the *safe* direction for a scanner: it can only make a
      rule match more, and every match is triaged by hand.
    """
    flags = "".join(sorted({flag for flag in ("i", "s", "m") if f"(?{flag})" in pattern}))
    translated = pattern.replace("\\z", "\\Z")
    for flag in ("i", "s", "m"):
        translated = translated.replace(f"(?{flag})", "")
    if flags:
        translated = f"(?{flags}){translated}"
    return translated


def _compile(pattern: str, *, what: str, config: Config) -> re.Pattern[str] | None:
    """Compile a Go-flavoured regex, recording failures instead of hiding them."""
    try:
        return re.compile(_transliterate(pattern))
    except re.error as error:  # pragma: no cover - depends on the rule set
        config.skipped.append((what, f"{error}: {pattern[:60]}…"))
        return None


def load_config(path: Path) -> Config:
    """Read a gitleaks TOML config into compiled rules."""
    config = Config()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    allowlist = raw.get("allowlist", {})
    for pattern in allowlist.get("paths", []):
        compiled = _compile(pattern, what="allowlist.paths", config=config)
        if compiled:
            config.allowlist_paths.append(compiled)
    for pattern in allowlist.get("regexes", []):
        compiled = _compile(pattern, what="allowlist.regexes", config=config)
        if compiled:
            config.allowlist_regexes.append(compiled)
    config.allowlist_stopwords = {word.casefold() for word in allowlist.get("stopwords", [])}

    for entry in raw.get("rules", []):
        pattern = None
        if entry.get("regex"):
            pattern = _compile(entry["regex"], what=f"rule {entry['id']}", config=config)
            if pattern is None:
                continue
        elif not entry.get("path"):
            config.skipped.append((f"rule {entry['id']}", "neither regex nor path"))
            continue
        # `allowlists` is a *list* of tables, each with its own optional
        # `condition` ("OR" by default, "AND" when every criterion must hold)
        # and `regexTarget` (secret | match | line).
        rule_allowlists: list[AllowlistEntry] = []
        for allowlist_entry in entry.get("allowlists", []) or []:
            regexes = []
            for raw_regex in allowlist_entry.get("regexes", []):
                compiled = _compile(
                    raw_regex, what=f"rule {entry['id']} allowlist", config=config
                )
                if compiled:
                    regexes.append(compiled)
            paths = []
            for raw_path in allowlist_entry.get("paths", []):
                compiled = _compile(
                    raw_path, what=f"rule {entry['id']} allowlist path", config=config
                )
                if compiled:
                    paths.append(compiled)
            rule_allowlists.append(
                AllowlistEntry(
                    regexes=tuple(regexes),
                    paths=tuple(paths),
                    stopwords=frozenset(
                        word.casefold() for word in allowlist_entry.get("stopwords", [])
                    ),
                    condition=str(allowlist_entry.get("condition", "OR")).upper(),
                    regex_target=str(allowlist_entry.get("regexTarget", "secret")),
                )
            )
        path_pattern = None
        if entry.get("path"):
            path_pattern = _compile(entry["path"], what=f"rule {entry['id']} path", config=config)
        config.rules.append(
            Rule(
                id=entry["id"],
                description=entry.get("description", "").strip(),
                pattern=pattern,
                secret_group=int(entry.get("secretGroup", 0)),
                keywords=tuple(word.casefold() for word in entry.get("keywords", [])),
                path_pattern=path_pattern,
                allowlists=tuple(rule_allowlists),
                entropy=entry.get("entropy"),
            )
        )
    return config


def _mask(secret: str) -> str:
    """Never echo a suspected secret in full — a scanner log is not a leak site."""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}…{secret[-4:]} ({len(secret)} chars)"


def shannon_entropy(data: str) -> float:
    """Shannon entropy in bits per character, exactly as gitleaks computes it.

    Ported from `detect/utils.go` in the gitleaks source (`-Σ p·log2 p` over
    rune frequencies); the comparison that uses it is `entropy <= rule.entropy
    → skip`, strict, so a rule that asks for 3.5 accepts 3.5001.
    """
    if not data:
        return 0.0
    counts: dict[str, int] = {}
    for char in data:
        counts[char] = counts.get(char, 0) + 1
    length = len(data)
    return -sum(
        (count / length) * math.log2(count / length) for count in counts.values()
    )


def _allowlisted(rule: Rule, secret: str, match_text: str, line: str, path: str) -> bool:
    """Apply the rule's own allowlists, honouring `condition` and `regexTarget`."""
    for entry in rule.allowlists:
        targets = {"secret": secret, "match": match_text, "line": line}
        checks: list[bool] = []
        if entry.regexes:
            target = targets.get(entry.regex_target, secret)
            checks.append(any(pattern.search(target) for pattern in entry.regexes))
        if entry.paths:
            checks.append(any(pattern.search(path) for pattern in entry.paths))
        if entry.stopwords:
            checks.append(entry.stopwords.intersection(targets.get(entry.regex_target, secret).casefold().split()))
        if not checks:
            continue
        if entry.condition == "AND":
            if all(checks):
                return True
        elif any(checks):
            return True
    return False


def _allowed(config: Config, text: str, path: str) -> bool:
    if any(pattern.search(path) for pattern in config.allowlist_paths):
        return True
    lowered = text.casefold()
    if config.allowlist_stopwords and lowered.strip() in config.allowlist_stopwords:
        return True
    return any(pattern.search(text) for pattern in config.allowlist_regexes)


def scan_text(config: Config, text: str, path: str) -> list[tuple[str, int, str, str]]:
    """Return (rule id, line number, masked secret, line) hits for one file."""
    hits: list[tuple[str, int, str, str]] = []
    if _allowed(config, text, path):
        return hits
    lines = text.splitlines()
    for rule in config.rules:
        if rule.path_pattern and not rule.path_pattern.search(path):
            continue
        if rule.pattern is None:
            hits.append((rule.id, 1, "(file name)", path))
            continue
        if rule.keywords and not any(word in text.casefold() for word in rule.keywords):
            continue
        for match in rule.pattern.finditer(text):
            try:
                secret = match.group(rule.secret_group)
            except IndexError:
                secret = match.group(0)
            if secret is None or secret == "":
                continue
            if secret.casefold().strip() in config.allowlist_stopwords:
                continue
            line_number = text.count("\n", 0, match.start()) + 1
            line = lines[line_number - 1]
            if _allowlisted(rule, secret, match.group(0), line, path):
                continue
            if rule.entropy is not None and shannon_entropy(secret) <= rule.entropy:
                continue
            hits.append((rule.id, line_number, _mask(secret), line[:120]))

    # gitleaks drops a `generic-*` finding when a specific rule already fired
    # on the same line: the generic rule exists to catch what no specific rule
    # knows about, so reporting both is double-counting one secret.
    generic_lines = {line for rule_id, line, _, _ in hits if rule_id.startswith("generic")}
    specific_lines = {line for rule_id, line, _, _ in hits if not rule_id.startswith("generic")}
    return [hit for hit in hits if not (hit[0].startswith("generic") and hit[1] in specific_lines)]



def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line]


def scan_working_tree(config: Config) -> int:
    findings = 0
    for relative in tracked_files():
        path = REPO_ROOT / relative
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for rule_id, line_number, masked, line in scan_text(config, text, relative):
            findings += 1
            print(f"{relative}:{line_number}: [{rule_id}] {masked}\n    {line.strip()[:110]}")
    return findings


def scan_history(config: Config, revision_range: str) -> int:
    """Scan every unique blob reachable from `revision_range`.

    The object list comes from `git rev-list --objects`; types and sizes from a
    single `git cat-file --batch-check` pass over stdin (`cat-file` does not
    accept an object argument with `--batch-check`), and contents from
    `git cat-file blob`. Trees and submodule commits are skipped: only blobs
    have content, and `rev-list --objects` lists all three kinds.
    """
    revisions = subprocess.run(
        ["git", "rev-list", revision_range],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    print(f"scanning {len(revisions)} commit(s) in {revision_range}")
    listing = subprocess.run(
        ["git", "rev-list", "--objects", *revisions],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    candidates: dict[str, str] = {}
    for entry in listing:
        parts = entry.split(maxsplit=1)
        if len(parts) != 2:
            continue
        sha, path = parts
        candidates.setdefault(sha, path)
    if not candidates:
        return 0

    meta = subprocess.run(
        ["git", "cat-file", "--batch-check"],
        cwd=REPO_ROOT,
        input="\n".join(candidates),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    findings = 0
    for line in meta.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[1] != "blob":
            continue
        sha, _, size = fields
        if not size.isdigit() or int(size) > MAX_FILE_BYTES:
            continue
        content = subprocess.run(
            ["git", "cat-file", "blob", sha], cwd=REPO_ROOT, capture_output=True, check=True
        ).stdout
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for rule_id, line_number, masked, line in scan_text(config, text, candidates[sha]):
            findings += 1
            print(f"{sha[:10]} {candidates[sha]}:{line_number}: [{rule_id}] {masked}")
            print(f"    {line.strip()[:110]}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        type=Path,
        help="gitleaks TOML config (default: scripts/gitleaks-config.toml)",
    )
    parser.add_argument(
        "--history",
        metavar="REVISION_RANGE",
        help="also scan blobs reachable from this range (e.g. main..HEAD)",
    )
    parser.add_argument(
        "--working-tree-only",
        action="store_true",
        help="with --history, skip the working-tree pass",
    )
    args = parser.parse_args()

    config_path = args.config or REPO_ROOT / "scripts" / "gitleaks-config.toml"
    if not config_path.is_file():
        print(f"✗ no gitleaks config at {config_path}", file=sys.stderr)
        return 2

    config = load_config(config_path)
    print(
        f"gitleaks-format scan with {len(config.rules)} rules from "
        f"{config_path.relative_to(REPO_ROOT)}"
    )

    findings = 0
    if not args.working_tree_only:
        findings += scan_working_tree(config)
        print(f"working tree: {findings} hit(s)")
    if args.history:
        history_findings = scan_history(config, args.history)
        findings += history_findings
        print(f"history ({args.history}): {history_findings} hit(s)")

    print()
    if config.skipped:
        print(f"{len(config.skipped)} rule(s) skipped (not expressible in Python re):")
        for what, reason in config.skipped:
            print(f"  - {what}: {reason}")
    print(
        "gaps versus the real gitleaks binary: no baseline, no remote allowlists, "
        "no `targetRule` allowlists; binary files skipped; `condition`/`regexTarget` "
        "and Shannon entropy are implemented from the gitleaks source."
    )

    if findings:
        print(f"\n✗ {findings} potential secret(s) — triage every hit by hand")
        return 1
    print("\n✓ nothing matched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

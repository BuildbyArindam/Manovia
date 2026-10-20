"""Unit tests for the repository's gitleaks configuration (Day 47).

The secrets scan silently stopped honouring ``.gitleaks.toml``, and nothing in
the repository failed when it did. The CI job runs gitleaks **8.24.3** — the
version pinned inside ``gitleaks/gitleaks-action@ff98106e`` (``src/index.js``:
``process.env.GITLEAKS_VERSION || "8.24.3"``) — and that release's config struct
(``config/config.go``, ``ViperConfig``) has a *singular* ``Allowlist struct{}``
with no plural ``[[allowlists]]``. viper drops keys it cannot map instead of
reporting them, so a plural block is discarded without a word: the scan then ran
the built-in rules with no allowlist at all and reported exactly the same ten
findings as before the file existed.

These tests pin the three things that made that failure invisible — the config
form the pinned version reads, the version pin itself, and the narrowness of the
allowlist — so the next drift fails a test instead of a scan.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / ".gitleaks.toml"
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# What gitleaks-action@ff98106e installs when GITLEAKS_VERSION is unset, and
# what CI now sets explicitly so the config above and the binary agree.
PINNED_GITLEAKS_VERSION = "8.24.3"

# The generated cross-language crypto vector lives here, and only here.
VECTOR_DIR = "frontend/src/features/sharing/testSupport/"

# An allowlist entry has to be one literal string: anchored, and built only from
# characters that are not regex metacharacters, so it can never widen to cover
# something nobody reviewed.
_LITERAL_ANCHORED = re.compile(r"\^(?P<literal>[A-Za-z0-9_./+=-]+)\$")


def _config() -> dict[str, Any]:
    with CONFIG_PATH.open("rb") as handle:
        return tomllib.load(handle)


def _ci() -> dict[str, Any]:
    document = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _scan_step(name: str) -> dict[str, Any]:
    steps = _ci()["jobs"]["secrets-scan"]["steps"]
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    raise AssertionError(f"the secrets-scan job has no step named {name!r}")


def test_the_config_extends_the_built_in_rule_set() -> None:
    """`useDefault` is what keeps the scan switched on.

    Without it the rules in this file would *replace* the built-in set, and a
    file that defines no rules of its own would turn secret detection off while
    still looking configured.
    """
    config = _config()
    assert config["extend"]["useDefault"] is True
    # No rule is redefined, and none is switched off through extend.disabledRules.
    assert "rules" not in config
    assert "disabledRules" not in config["extend"]


def test_the_allowlist_uses_the_singular_form_the_pinned_version_reads() -> None:
    """8.24.3 has no plural `[[allowlists]]`, and ignores it without complaining.

    This is the regression: the plural form is valid TOML, is accepted by
    gitleaks 8.30, and is dropped on the floor by the version CI runs.
    """
    config = _config()
    assert isinstance(config.get("allowlist"), dict)
    assert "allowlists" not in config


def test_ci_pins_the_gitleaks_version_the_config_is_written_for() -> None:
    """The config form and the binary version have to be pinned together.

    An unpinned scanner resolves "whatever the action defaults to", so the
    config's compatibility is only meaningful next to an explicit version.
    """
    env = _scan_step("gitleaks").get("env", {})
    assert env.get("GITLEAKS_VERSION") == PINNED_GITLEAKS_VERSION


def test_ci_names_the_config_by_absolute_path() -> None:
    """Discovery is working-directory dependent; the env var is not.

    gitleaks falls back to `<source>/.gitleaks.toml`, where `<source>` is the
    directory it was started in. GITLEAKS_CONFIG is checked before that, so the
    action's working directory stops being able to decide whether the allowlist
    exists.
    """
    env = _scan_step("gitleaks").get("env", {})
    config = str(env.get("GITLEAKS_CONFIG", ""))
    assert config.endswith("/.gitleaks.toml")
    assert "github.workspace" in config


def test_only_the_generated_vector_directory_is_path_allowlisted() -> None:
    config = _config()
    assert config["allowlist"]["paths"] == [VECTOR_DIR]


def test_no_commit_regex_target_or_stopword_is_exempted() -> None:
    """Allowlisting by value and by one directory — nothing broader.

    `commits` would exempt everything a commit touched, `stopwords` would exempt
    any secret containing a common word, and `regexTarget` would move the match
    off the secret onto the surrounding line, where a short anchor no longer
    means "exactly this value".
    """
    allowlist = _config()["allowlist"]
    assert set(allowlist) == {"paths", "regexes"}


def test_every_allowlisted_regex_is_one_literal_value() -> None:
    """Each entry permits exactly one string and cannot be read as a pattern.

    Anchors alone are not enough: `^key.*$` is anchored and still matches
    anything starting with "key". Requiring a metacharacter-free body makes each
    entry a literal, so widening the allowlist means adding a visible line.
    """
    regexes = _config()["allowlist"]["regexes"]
    assert len(regexes) == 5, "growing this list grows what the scan cannot see"
    for entry in regexes:
        match = _LITERAL_ANCHORED.fullmatch(entry)
        assert match is not None, f"{entry!r} is not an anchored literal"
        literal = match.group("literal")
        assert re.compile(entry).fullmatch(literal)


def test_an_unrelated_secret_is_still_detected() -> None:
    """The allowlist is not a hole: new values and other paths are still caught.

    Uses a value that is not in the config, next to a keyword the
    `generic-api-key` rule looks for, in a file outside the allowlisted
    directory.
    """
    allowlist = _config()["allowlist"]
    fresh = "Tq7vX2mZ9pL4wR1bY6cD3fG8hJ5kS0nA8eV2uQ7"
    line = f'SHARE_SECRET_KEY = "{fresh}"'

    regexes = allowlist["regexes"]
    assert not any(re.search(pattern, fresh) for pattern in regexes)
    paths = allowlist["paths"]
    assert not any(re.search(pattern, "backend/app/core/config.py") for pattern in paths)
    assert not any(re.search(pattern, line) for pattern in regexes)
    # And the entries that are there are not vacuous: each matches its own value.
    for pattern in regexes:
        literal = pattern[1:-1]
        assert re.fullmatch(pattern, literal), f"{pattern!r} does not match {literal!r}"

"""Dataset identity: hashes, row counts and a JSONL reader that refuses to guess.

A run must be able to say *exactly* which bytes it trained on. Two mechanisms
work together:

* **DVC** versions the files (``ml/dvc.yaml``, ``ml/dvc.lock``) and answers
  "did the data change since the pipeline last ran?" with ``dvc status``.
* **This module** puts the sha256 of every file a run read into the MLflow run
  as a param, so a run id is enough to find the data even without DVC.

Belt and braces on purpose: DVC is the workflow, the hash in the run is the
evidence. Neither depends on the other being installed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from manovia_ml.paths import ml_root

__all__ = [
    "DatasetRef",
    "read_jsonl",
    "sha256_bytes",
    "sha256_file",
    "sha256_text",
]

_CHUNK = 1024 * 1024


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path | str) -> str:
    """sha256 of a file's bytes, streamed so a large corpus does not need RAM."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DatasetRef:
    """A dataset version, as recorded in a run.

    ``path`` is stored relative to the ML root when it is inside it, so the
    reference stays meaningful when the repository moves.
    """

    name: str
    path: str
    sha256: str
    rows: int
    languages: dict[str, int] = field(default_factory=dict)
    labels: dict[str, int] = field(default_factory=dict)

    def as_params(self, prefix: str = "data") -> dict[str, str]:
        """The subset that goes into MLflow params (all strings, as MLflow wants)."""

        params = {
            f"{prefix}.{self.name}.path": self.path,
            f"{prefix}.{self.name}.sha256": self.sha256,
            f"{prefix}.{self.name}.rows": str(self.rows),
        }
        for lang, count in sorted(self.languages.items()):
            params[f"{prefix}.{self.name}.lang.{lang}"] = str(count)
        for label, count in sorted(self.labels.items()):
            params[f"{prefix}.{self.name}.label.{label}"] = str(count)
        return params

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "sha256": self.sha256,
            "rows": self.rows,
            "languages": dict(sorted(self.languages.items())),
            "labels": dict(sorted(self.labels.items())),
        }


def _display_path(path: Path) -> str:
    """Relative to the ML root when possible, absolute otherwise."""

    try:
        return str(path.resolve().relative_to(ml_root()))
    except ValueError:
        return str(path.resolve())


def read_jsonl(path: Path | str) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dicts, with an honest error message.

    Blank lines are skipped; anything that is not a JSON object raises. A silent
    skip of a malformed line is exactly the kind of thing that turns a labelling
    mistake into a "model quality" problem three weeks later.
    """

    rows: list[dict[str, Any]] = []
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{file_path}:{lineno}: invalid JSON: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"{file_path}:{lineno}: expected a JSON object, got {type(parsed)}"
                )
            rows.append(parsed)
    return rows


def iter_jsonl(path: Path | str) -> Iterator[dict[str, Any]]:
    yield from read_jsonl(path)


def dataset_ref(
    path: Path | str,
    *,
    name: str | None = None,
    text_field: str = "text",
    label_field: str = "label",
    lang_field: str | None = "lang",
    rows: list[dict[str, Any]] | None = None,
) -> DatasetRef:
    """Build a :class:`DatasetRef` for a JSONL file, hashing its bytes.

    ``rows`` may be passed in by a caller that already read the file, which
    avoids reading a corpus twice; the hash is always computed from the bytes on
    disk, never from the parsed rows (a parser change must not look like a data
    change, and vice versa).
    """

    file_path = Path(path)
    parsed = rows if rows is not None else read_jsonl(file_path)
    if parsed and text_field:
        # A config with `text_field: sentence` over data whose column is `text`
        # would otherwise fail much later, deep inside a vectoriser.
        missing_text = [row.get("id") for row in parsed if text_field not in row]
        if missing_text:
            raise ValueError(
                f"{file_path}: {len(missing_text)} row(s) have no {text_field!r} field "
                f"(first: {missing_text[0]})"
            )

    languages: dict[str, int] = {}
    labels: dict[str, int] = {}
    for row in parsed:
        if lang_field and lang_field in row:
            key = str(row[lang_field])
            languages[key] = languages.get(key, 0) + 1
        if label_field in row:
            key = str(row[label_field])
            labels[key] = labels.get(key, 0) + 1

    return DatasetRef(
        name=name or file_path.stem,
        path=_display_path(file_path),
        sha256=sha256_file(file_path),
        rows=len(parsed),
        languages=languages,
        labels=labels,
    )


def check_expected(ref: DatasetRef, *, sha256: str | None, rows: int | None) -> None:
    """Fail closed when a config names a dataset version the file does not match.

    Pinning a hash in a config is how a run says "I know which data this number
    came from". Letting a hash mismatch pass with a warning is how a silent data
    swap becomes an unexplained metric change.
    """

    if sha256 is not None and ref.sha256 != sha256:
        raise ValueError(
            f"{ref.path}: sha256 mismatch (config expects {sha256}, file is {ref.sha256}); "
            "the data changed — re-run `make ml-data` and update the config deliberately"
        )
    if rows is not None and ref.rows != rows:
        raise ValueError(f"{ref.path}: expected {rows} rows, found {ref.rows}")

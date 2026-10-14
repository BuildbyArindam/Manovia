"""Registry loader: read ``@production`` by alias, verify SHA-256, then load.

The materialised registry directory is written by the ML promotion step
(``manovia_ml.compression.registry``):

    <registry_dir>/<model>/aliases.json        {production, staging, ...}
    <registry_dir>/<model>/versions/<v>/model.onnx
    <registry_dir>/<model>/versions/<v>/manifest.json   {sha256, labels, ...}

This loader is the serving-side half of the contract: it resolves the
alias, recomputes the model file's sha256, and refuses to hand back a
path whose bytes do not match the manifest. A checksum mismatch is a
:class:`ChecksumMismatchError`, never a fallback to "load it anyway" —
the caller's fallback is the *previous version*, whose own checksum is
verified the same way.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

__all__ = [
    "MANIFEST_FILENAME",
    "ChecksumMismatchError",
    "LoadedModel",
    "RegistryError",
    "RegistryLoader",
    "sha256_file",
]

MANIFEST_FILENAME: Final = "manifest.json"


class RegistryError(RuntimeError):
    """The registry cannot resolve what was asked for."""


class ChecksumMismatchError(RegistryError):
    """Model bytes do not match the manifest sha256. Refuse to load."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class LoadedModel:
    """A verified artefact: where it is, what it claims, and its version."""

    model_name: str
    version: str
    directory: Path
    model_path: Path
    manifest: dict[str, Any]

    @property
    def labels(self) -> tuple[str, ...]:
        labels = self.manifest.get("labels")
        return tuple(str(item) for item in labels) if isinstance(labels, list) else ()


class RegistryLoader:
    """Resolves aliases in one registry directory. Stateless after construction."""

    def __init__(self, registry_dir: str | Path) -> None:
        self._root = Path(registry_dir).expanduser()

    @property
    def root(self) -> Path:
        return self._root

    def aliases(self, model_name: str) -> dict[str, Any]:
        path = self._root / model_name / "aliases.json"
        if not path.is_file():
            raise RegistryError(f"no registry entry for model {model_name!r} at {self._root}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RegistryError(f"{path}: aliases must be a mapping")
        return payload

    def load(self, model_name: str, *, alias: str = "production") -> LoadedModel:
        """Resolve ``alias``, verify the checksum, return the artefact."""

        aliases = self.aliases(model_name)
        version = aliases.get(alias)
        if not isinstance(version, str) or not version:
            raise RegistryError(f"model {model_name!r} has no version at alias {alias!r}")
        directory = self._root / model_name / "versions" / version
        manifest_path = directory / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise RegistryError(f"{directory}: no {MANIFEST_FILENAME}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise RegistryError(f"{manifest_path}: manifest must be a mapping")
        model_file = manifest.get("model_file")
        expected = manifest.get("sha256")
        if not isinstance(model_file, str) or not isinstance(expected, str):
            raise RegistryError(f"{manifest_path}: manifest lacks model_file/sha256")
        model_path = directory / model_file
        if not model_path.is_file():
            raise RegistryError(f"{directory}: model file {model_file!r} missing")
        actual = sha256_file(model_path)
        if actual != expected:
            raise ChecksumMismatchError(
                f"model {model_name!r} version {version}: sha256 mismatch "
                f"(manifest {expected[:12]}…, actual {actual[:12]}…); refusing to load"
            )
        return LoadedModel(model_name, version, directory, model_path, manifest)

    def load_with_fallback(self, model_name: str) -> LoadedModel:
        """Production, or — if it fails verification — the previous version.

        This is the runtime's first fallback rung (ADR 0034): a corrupt or
        missing production artefact degrades to the version rollback would
        restore, verified the same way. If that also fails, the error
        propagates and the caller falls back to the pre-Day-34 classifier.
        """

        try:
            return self.load(model_name, alias="production")
        except RegistryError:
            aliases = self.aliases(model_name)
            previous = aliases.get("previous_production")
            if not isinstance(previous, str) or not previous:
                raise
            return self.load(model_name, alias="previous_production")

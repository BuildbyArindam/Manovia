"""Model registry: MLflow aliases as stages, checksummed production loads.

MLflow 3.x removed registry *stages* in favour of *aliases*, so this module
maps the brief's stages onto aliases — ``@staging``, ``@production`` and an
``archived`` record — and keeps the mapping in one place. Promotion and
rollback are alias moves: the bytes never change, only which version the
alias names, which is what makes rollback a one-command, no-retrain act.

Two stores, two jobs:

* **MLflow** (this module's client) is the record: versions, aliases, the
  sha256 tag recorded at registration.
* **The materialised registry directory** is what the backend loads. The
  backend does not install MLflow, so promotion/rollback also copy the
  aliased version into ``<registry_dir>/<model>/versions/<version>/`` and
  rewrite ``aliases.json``. The backend loader reads ``@production`` from
  there and verifies the manifest sha256 before constructing a session —
  a mismatch refuses to load (see ``backend/app/services/ml_runtime``).

Checksum rule, both sides: ``manifest.json`` carries ``sha256`` over the
model file. Registration records it as a version tag; loading recomputes
it. A file corrupted after registration therefore fails loudly at load,
never silently at inference.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manovia_ml.hashing import sha256_file
from manovia_ml.mlflow_api import MlflowClient

__all__ = [
    "ALIAS_ARCHIVED",
    "ALIAS_PRODUCTION",
    "ALIAS_STAGING",
    "ChecksumError",
    "GateRejectedError",
    "RegisteredVersion",
    "RegistryError",
    "RegistryManager",
    "main",
    "verify_artifact",
]

#: Stage names from the brief, as MLflow aliases.
ALIAS_STAGING = "staging"
ALIAS_PRODUCTION = "production"
ALIAS_ARCHIVED = "archived"

MANIFEST_FILENAME = "manifest.json"


class RegistryError(RuntimeError):
    """Registration, promotion or rollback cannot proceed."""


class ChecksumError(RegistryError):
    """The model file's bytes do not match its manifest sha256."""


class GateRejectedError(RegistryError):
    """Promotion was attempted for a candidate that failed release gates."""


def verify_artifact(directory: Path) -> dict[str, Any]:
    """Verify ``directory``'s model against its manifest; return the manifest.

    Raises :class:`ChecksumError` on mismatch and :class:`RegistryError`
    when the artefact is incomplete. This is the exact check the backend
    loader mirrors — a model that fails here must never be served.
    """

    directory = Path(directory)
    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise RegistryError(f"{directory}: no {MANIFEST_FILENAME}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model_file = manifest.get("model_file")
    expected = manifest.get("sha256")
    if not isinstance(model_file, str) or not isinstance(expected, str):
        raise RegistryError(f"{directory}: manifest lacks model_file/sha256")
    model_path = directory / model_file
    if not model_path.is_file():
        raise RegistryError(f"{directory}: model file {model_file!r} missing")
    actual = sha256_file(model_path)
    if actual != expected:
        raise ChecksumError(
            f"{directory}: sha256 mismatch for {model_file} "
            f"(manifest {expected[:12]}…, actual {actual[:12]}…); refusing to load"
        )
    return manifest


@dataclass(frozen=True)
class RegisteredVersion:
    model_name: str
    version: str
    artifact_dir: Path
    sha256: str


class RegistryManager:
    """MLflow registry + materialised serving directory, kept in step."""

    def __init__(self, *, tracking_uri: str, registry_dir: Path) -> None:
        self._client = MlflowClient(tracking_uri=tracking_uri)
        self._registry_dir = Path(registry_dir)

    # -- materialised layout -------------------------------------------------

    def _model_dir(self, model_name: str) -> Path:
        return self._registry_dir / model_name

    def _version_dir(self, model_name: str, version: str) -> Path:
        return self._model_dir(model_name) / "versions" / version

    def _aliases_path(self, model_name: str) -> Path:
        return self._model_dir(model_name) / "aliases.json"

    def read_aliases(self, model_name: str) -> dict[str, Any]:
        path = self._aliases_path(model_name)
        if not path.is_file():
            return {
                "production": None,
                "staging": None,
                "archived": [],
                "previous_production": None,
            }
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_aliases(self, model_name: str, aliases: dict[str, Any]) -> Path:
        path = self._aliases_path(model_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(aliases, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def _materialise(self, model_name: str, version: str, source_dir: Path) -> Path:
        target = self._version_dir(model_name, version)
        target.mkdir(parents=True, exist_ok=True)
        for item in Path(source_dir).iterdir():
            if item.is_file():
                shutil.copyfile(item, target / item.name)
        verify_artifact(target)
        return target

    def _sync_mlflow_alias(self, model_name: str, alias: str, version: str) -> None:
        try:
            self._client.set_registered_model_alias(model_name, alias, version)
        except Exception as exc:  # mlflow raises several types across stores
            raise RegistryError(
                f"could not set alias {alias!r} for {model_name!r} to {version}: {exc}"
            ) from exc

    # -- lifecycle -----------------------------------------------------------

    def register(self, model_name: str, artifact_dir: Path, *, version: str) -> RegisteredVersion:
        """Register one artefact version and copy it into the registry dir.

        The checksum is verified *before* anything is recorded: registering
        corrupt bytes would make the registry itself the thing that lies.
        """

        manifest = verify_artifact(artifact_dir)
        try:
            self._client.get_registered_model(model_name)
        except Exception:
            self._client.create_registered_model(model_name)
        try:
            created = self._client.create_model_version(
                name=model_name,
                source=str(Path(artifact_dir).resolve()),
                run_id=None,
                tags={"sha256": str(manifest["sha256"]), "task": str(manifest.get("task", ""))},
                description=f"Day 34 student {version}",
            )
        except Exception as exc:
            raise RegistryError(f"MLflow registration failed for {model_name!r}: {exc}") from exc
        target = self._materialise(model_name, version, artifact_dir)
        aliases = self.read_aliases(model_name)
        aliases["staging"] = version
        # MLflow assigns its own integer versions; our labels (v1, v2) are
        # the materialised directory names. Record the mapping so an alias
        # move names the right MLflow version even after later registrations.
        mapping = dict(aliases.get("mlflow_versions") or {})
        mapping[version] = str(created.version)
        aliases["mlflow_versions"] = mapping
        self._write_aliases(model_name, aliases)
        self._sync_mlflow_alias(model_name, ALIAS_STAGING, str(created.version))
        return RegisteredVersion(model_name, version, target, str(manifest["sha256"]))

    def _mlflow_version(self, model_name: str, version_label: str) -> str:
        aliases = self.read_aliases(model_name)
        mapping = aliases.get("mlflow_versions") or {}
        version = mapping.get(version_label)
        if not version:
            raise RegistryError(
                f"no MLflow version recorded for {model_name!r} label {version_label!r}"
            )
        return str(version)

    def promote(
        self,
        model_name: str,
        version: str,
        *,
        gates_passed: bool | None = None,
    ) -> dict[str, Any]:
        """Move ``@production`` to ``version``. Requires passing gates.

        ``gates_passed=None`` means "no gate report supplied" and is a
        refusal, not a pass: promotion without evidence is how a degraded
        model reaches users (ADR 0034).
        """

        if gates_passed is not True:
            raise GateRejectedError(
                "promotion requires a passing release-gate report "
                f"(gates_passed={gates_passed!r}); run the gate check first"
            )
        version_dir = self._version_dir(model_name, version)
        if not version_dir.is_dir():
            raise RegistryError(f"{model_name!r} version {version!r} is not registered")
        verify_artifact(version_dir)
        aliases = self.read_aliases(model_name)
        current = aliases.get("production")
        if current is not None and current != version:
            aliases["previous_production"] = current
            archived = list(aliases.get("archived") or [])
            if current not in archived:
                archived.append(current)
            aliases["archived"] = archived
        aliases["production"] = version
        if aliases.get("staging") == version:
            aliases["staging"] = None
        self._write_aliases(model_name, aliases)
        self._sync_mlflow_alias(
            model_name, ALIAS_PRODUCTION, self._mlflow_version(model_name, version)
        )
        return aliases

    def rollback(self, model_name: str) -> dict[str, Any]:
        """One-command rollback: ``@production`` returns to the previous version."""

        aliases = self.read_aliases(model_name)
        previous = aliases.get("previous_production")
        if not previous:
            raise RegistryError(
                f"{model_name!r} has no previous production version to roll back to"
            )
        version_dir = self._version_dir(model_name, str(previous))
        if not version_dir.is_dir():
            raise RegistryError(f"previous version {previous!r} is missing from the registry")
        verify_artifact(version_dir)
        rolled_from = aliases.get("production")
        aliases["production"] = previous
        aliases["previous_production"] = rolled_from
        self._write_aliases(model_name, aliases)
        self._sync_mlflow_alias(
            model_name, ALIAS_PRODUCTION, self._mlflow_version(model_name, str(previous))
        )
        return aliases

    def resolve(self, model_name: str, *, alias: str = ALIAS_PRODUCTION) -> Path:
        """The materialised directory an alias names, checksum-verified."""

        aliases = self.read_aliases(model_name)
        version = aliases.get(alias)
        if not version:
            raise RegistryError(f"{model_name!r} has no version at alias {alias!r}")
        target = self._version_dir(model_name, str(version))
        verify_artifact(target)
        return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.compression.registry",
        description="Day 34 model registry: register, promote, roll back.",
    )
    parser.add_argument("--tracking-uri", required=True)
    parser.add_argument("--registry-dir", required=True)
    parser.add_argument("--model", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    register_parser = sub.add_parser("register")
    register_parser.add_argument("--artifact", required=True)
    register_parser.add_argument("--version", required=True)
    promote_parser = sub.add_parser("promote")
    promote_parser.add_argument("--version", required=True)
    promote_parser.add_argument(
        "--gates-passed",
        action="store_true",
        help="assert the candidate passed release gates (checked by the caller)",
    )
    sub.add_parser("rollback")
    sub.add_parser("status")
    args = parser.parse_args(argv)

    manager = RegistryManager(tracking_uri=args.tracking_uri, registry_dir=Path(args.registry_dir))
    if args.command == "register":
        result = manager.register(args.model, Path(args.artifact), version=args.version)
        print(f"registered {result.model_name} {result.version} sha256={result.sha256[:12]}…")
    elif args.command == "promote":
        aliases = manager.promote(args.model, args.version, gates_passed=args.gates_passed)
        print(json.dumps(aliases, indent=2, sort_keys=True))
    elif args.command == "rollback":
        aliases = manager.rollback(args.model)
        print(json.dumps(aliases, indent=2, sort_keys=True))
    else:
        print(json.dumps(manager.read_aliases(args.model), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())

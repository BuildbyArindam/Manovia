"""Write the committed FHIR sample bundle the CI validator checks (Day 47).

One artifact, no hand-editing::

    python scripts/export_fhir_sample.py          # rewrite the sample
    python scripts/export_fhir_sample.py --check  # exit 1 if it is stale

The sample lives at ``backend/tests/fixtures/fhir/sample_share_bundle.json`` and
is what the ``fhir-validation`` job in ``.github/workflows/ci.yml`` feeds to the
official HL7 validator (``hapifhir/org.hl7.fhir.core``'s ``validator_cli.jar``,
FHIR 4.0.1). Validating a committed file rather than a bundle built inside the
job means the thing under review is the thing in the repository: a reviewer can
read the exact JSON a professional's system would import, and a diff shows when
the document's shape changes.

Two properties make that safe:

* **It is the real code path.** The bundle comes from
  :func:`app.services.sharing.fhir.bundle_bytes`, the same function the share
  endpoint encrypts — not from a second, tidier writer kept only for CI.
* **It cannot go stale.** The build is deterministic (ids are ``uuid5`` of the
  bundle id, instants are second-precision, JSON is two-space indented), the
  bundle id and timestamp are pinned below, and the payload is
  :func:`tests.sharing_helpers.sample_payload` — the same synthetic person the
  unit and security suites use. ``--check`` compares bytes, and
  ``backend/tests/unit/test_share_fhir_sample.py`` runs that comparison in the
  ordinary suite, so a change to the builder without a regenerated sample fails
  the build.

Nothing here touches the network: the LOINC codes are the ones already pinned in
``app/content/sharing/loinc.json``.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.services.sharing.fhir import bundle_bytes

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
SAMPLE_PATH = BACKEND_DIR / "tests" / "fixtures" / "fhir" / "sample_share_bundle.json"

#: The bundle's identity. Derived from Manovia's own FHIR namespace with
#: ``uuid5`` rather than typed in, so it is reproducible and visibly synthetic:
#: the same document rebuilt anywhere in the world gets the same id.
SAMPLE_BUNDLE_ID = uuid.uuid5(uuid.NAMESPACE_URL, "https://manovia.app/fhir/share-bundle/sample")

#: Pinned build instant. The day the LOINC terms were read off loinc.org and
#: copied into ``app/content/sharing/loinc.json``, so the committed sample and
#: the terminology file tell the same story about when they were made.
SAMPLE_NOW = datetime(2026, 10, 19, 9, 30, tzinfo=UTC)


def render_sample() -> str:
    """The sample bundle as the text that goes into the repository."""

    # Imported here, not at module scope: `tests` is not part of the installed
    # package (`packages.find` covers `app*` only), so the path entry has to
    # exist first. It is the synthetic person the suites already use, which is
    # the point — the sample must not describe anybody real, and one fixture
    # person keeps the tests and the artifact consistent.
    sys.path.insert(0, str(BACKEND_DIR))
    from tests.sharing_helpers import sample_payload

    bundle = bundle_bytes(sample_payload(), bundle_id=SAMPLE_BUNDLE_ID, now=SAMPLE_NOW)
    return bundle.decode("utf-8") + "\n"


def sample_size(text: str) -> int:
    """The artifact's size on disk, in bytes.

    Not ``len(text)``: the disclaimer's em dashes are one character but three
    bytes in UTF-8, so the character count understates the file by 20 bytes and
    a printed size that does not match ``wc -c`` is a size nobody can check.
    """

    return len(text.encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed sample differs from what the code produces",
    )
    args = parser.parse_args()

    relative = SAMPLE_PATH.relative_to(REPO_DIR)
    text = render_sample()

    if args.check:
        if not SAMPLE_PATH.exists():
            print(f"missing: {relative} — run backend/scripts/export_fhir_sample.py")
            return 1
        if SAMPLE_PATH.read_text(encoding="utf-8") != text:
            print(f"stale: {relative} — run backend/scripts/export_fhir_sample.py")
            return 1
        print(f"fhir sample up to date ({relative}, {sample_size(text)} bytes)")
        return 0

    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {relative} ({sample_size(text)} bytes, bundle id {SAMPLE_BUNDLE_ID})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Static, versioned content shipped with the backend.

Three families, one rule: nothing here is generated, fetched or user-editable, so
everything a person can be shown is reviewable in this repository.

* **Consent documents** (``consent_documents.json``) — the single source of truth
  for what a user agrees to and which ``version`` is stamped on each
  :class:`~app.models.consent.Consent` row. Changing an agreement means editing
  the file and bumping its ``version`` (a new version requires re-consent
  everywhere the current one is enforced).
* **Helplines** (``helplines.json`` + ``helplines.schema.json``) — the only file
  that carries crisis contacts, each with a ``source_url`` and a ``last_verified``
  date (AGENTS.md safety rule 6).
* **Localised crisis templates** (``i18n/*.json``) — the pre-written replies shown
  at high risk (AGENTS.md safety rule 2), one file per language.

Every loader validates on import and fails loudly: broken content must be a
startup error, never a silently empty list shown to someone in distress.
"""

from app.content.crisis import (
    CrisisResource,
    HelplineContent,
    load_helplines,
    parse_helplines,
)
from app.content.documents import (
    ConsentDocument,
    ConsentDocuments,
    load_consent_documents,
)
from app.content.i18n import (
    CrisisTemplate,
    LocalisedContent,
    RenderedTemplate,
    available_locales,
    load_all_localisations,
    load_localisation,
    parse_localisation,
    resolve_locale,
)

__all__ = [
    "ConsentDocument",
    "ConsentDocuments",
    "CrisisResource",
    "CrisisTemplate",
    "HelplineContent",
    "LocalisedContent",
    "RenderedTemplate",
    "available_locales",
    "load_all_localisations",
    "load_consent_documents",
    "load_helplines",
    "load_localisation",
    "parse_helplines",
    "parse_localisation",
    "resolve_locale",
]

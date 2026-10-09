"""Static, versioned consent documents shipped with the backend.

The JSON file is the single source of truth for what a user is agreeing to and
which ``version`` is stamped on each :class:`~app.models.consent.Consent` row.
Nothing here is user-editable; changing an agreement means editing the file and
bumping its ``version`` (a new version requires re-consent everywhere the
current version is enforced).
"""

from app.content.documents import (
    ConsentDocument,
    ConsentDocuments,
    load_consent_documents,
)

__all__ = ["ConsentDocument", "ConsentDocuments", "load_consent_documents"]

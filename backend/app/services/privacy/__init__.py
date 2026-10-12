"""Privacy controls: settings, export, retention and account deletion.

Day 22 gives a person three things they could not do before:

* see and change what is stored and for how long (``store.py``),
* take their data away (``export.py``),
* have it actually removed — on a schedule they choose (``retention.py``) or
  immediately and irreversibly (``deletion.py``).

The package is deliberately free of HTTP: the CLI (``make retention``), the API
routes and the tests all call the same functions.
"""

from app.services.privacy.deletion import (
    DeletionResult,
    count_user_rows,
    delete_account,
)
from app.services.privacy.export import (
    EXPORT_FORMAT_VERSION,
    ExportFile,
    UserExport,
    build_user_export,
    render_zip,
)
from app.services.privacy.retention import (
    RetentionReport,
    UserRetentionPlan,
    apply_retention,
    plan_retention,
)
from app.services.privacy.store import (
    PrivacySnapshot,
    chat_storage_allowed,
    load_privacy,
    sentiment_storage_allowed,
)

__all__ = [
    "EXPORT_FORMAT_VERSION",
    "DeletionResult",
    "ExportFile",
    "PrivacySnapshot",
    "RetentionReport",
    "UserExport",
    "UserRetentionPlan",
    "apply_retention",
    "build_user_export",
    "chat_storage_allowed",
    "count_user_rows",
    "delete_account",
    "load_privacy",
    "plan_retention",
    "render_zip",
    "sentiment_storage_allowed",
]

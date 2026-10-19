"""Day 47: the clinician-share bridge.

The modules here answer four questions, in this order:

* :mod:`~app.services.sharing.items` — what a person can choose to share, and the
  one document a selection becomes.
* :mod:`~app.services.sharing.fhir` and :mod:`~app.services.sharing.pdf` — the two
  exports that document can be rendered into.
* :mod:`~app.services.sharing.crypto`, :mod:`~app.services.sharing.passcode` and
  :mod:`~app.services.sharing.terminology` — how it is protected, and which
  clinical codes it may claim.
* :mod:`~app.services.sharing.service` — the flow: switch, preview, consent,
  create, open, audit, revoke.

Unlike the other service packages, this one deliberately re-exports **nothing**.
:mod:`app.db.repos.shares` needs :func:`app.services.sharing.crypto.hash_token`
to look a share up by token, and :mod:`app.services.sharing.items` needs the
repositories to read the person's entries — so a re-exporting package ``__init__``
would make ``app.db.repos`` and ``app.services.sharing`` import each other and
fail at startup. Importing the module you mean —
``from app.services.sharing.service import ShareService`` — keeps both directions
legal.

Nothing in this package is an EHR integration. It produces a document a person
chooses to hand to a professional; it never writes to a record and never reads
one. See ``docs/clinical-sharing.md``.
"""

from __future__ import annotations

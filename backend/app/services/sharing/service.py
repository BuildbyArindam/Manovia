"""The share bridge: create, preview, open, revoke — and the audit trail.

The order of operations in :meth:`ShareService.create` is the security design, so
it is worth stating once:

1. Check the person's own switch (``SharePreference``) — sharing is off until
   they turn it on.
2. Build the payload from the selection (:mod:`app.services.sharing.items`).
3. Re-derive the consent summary and compare it with the hash the client
   confirmed at preview time. If the data moved between preview and create, the
   share is refused and the person is asked to look again — a consent to one
   document is not a consent to a different one.
4. Render the documents (PDF, FHIR bundle).
5. Generate the token and the 256-bit secret, derive the content key from the
   secret *and* the passcode, encrypt each document under its own nonce and AAD.
6. Store the row: ciphertext, budget, expiry, passcode hash. Never the secret.
7. Return the secret and the passcode **once**. They are not retrievable later,
   by anybody, including the owner: that is the property that makes the stored
   row useless on its own.

Opening a share (:meth:`ShareService.open_public`) checks, in order: rate limit
(in the router), token, lockout, revocation, expiry, view budget (claimed
atomically), passcode. Every refusal path that concerns an existing share writes
an audit line the owner can read; an unknown token writes nothing, because there
is nothing to write it to.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.repos import ACTOR_OWNER, ACTOR_VIEWER, ShareRepository
from app.models.base import as_utc, utcnow
from app.models.enums import ShareCipher, ShareEventKind, ShareFormat, ShareItem
from app.models.share import ClinicianShare, ShareAccessEvent, SharePreference, share_aad
from app.models.user import User
from app.services.sharing import crypto
from app.services.sharing.fhir import bundle_bytes
from app.services.sharing.items import (
    DISCLAIMER,
    DISCLAIMER_TITLE,
    ITEM_CATALOGUE,
    ShareContentError,
    SharePayload,
    ShareRequest,
    build_payload,
    consent_lines,
)
from app.services.sharing.passcode import (
    PasscodeError,
    generate_passcode,
    hash_passcode,
    new_salt,
    validate,
    verify_passcode,
)
from app.services.sharing.pdf import render_pdf

#: The public URL path prefix for a share link. The key lives in the fragment
#: after this path, so it never reaches the server.
SHARE_PATH_PREFIX = "/s/"

#: Content types handed to the viewer, so it can offer a download or a render.
CONTENT_TYPES: dict[str, str] = {
    "payload": "application/json",
    "pdf": "application/pdf",
    "fhir": "application/fhir+json",
}


class ShareError(Exception):
    """Base class for every refusal this service makes."""


class SharingDisabledError(ShareError):
    """The person has not turned sharing on (or the deployment turned it off)."""


class ShareNotFoundError(ShareError):
    """No share of the owner's with that id."""


class ShareUnavailableError(ShareError):
    """A public token that cannot be used.

    One class for unknown, expired, revoked and out-of-budget links: the public
    answer is identical in all four cases, which is what makes the endpoint
    useless for finding out whether a token exists.
    """


class PasscodeIncorrectError(ShareError):
    """Wrong passcode; carries how many tries are left."""

    def __init__(self, attempts_remaining: int) -> None:
        super().__init__("passcode incorrect")
        self.attempts_remaining = attempts_remaining


class PasscodeLockedError(ShareError):
    """Too many wrong passcodes; carries how long the link refuses everyone."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("passcode locked")
        self.retry_after_seconds = retry_after_seconds


class ConsentMismatchError(ShareError):
    """What was confirmed is not what would be shared."""


class ShareLimitError(ShareError):
    """Too many live shares, or a document too large to build."""


class ShareRequestError(ShareError):
    """A create request the settings refuse (bad TTL, bad view limit, no format)."""


#: How the viewer derives the content key, spelled out with every parameter.
#:
#: Built from the crypto module's own constants so the sentence cannot drift from
#: the code: WebCrypto's HKDF needs the salt, the info and the length, and a
#: browser implementer who guesses the info string gets a different key and an
#: unhelpful "decryption failed".
_HKDF_INFO_TEXT = crypto.HKDF_INFO.decode("ascii")
KEY_DERIVATION = (
    "HKDF-SHA256(ikm = url_key || 0x00 || utf8(passcode), "
    f'salt = SHA-256(token), info = "{_HKDF_INFO_TEXT}", '
    f"length = {crypto.KEY_BYTES} bytes)"
)

#: The Associated Data format, stated once. The viewer receives each document's
#: AAD explicitly (see :class:`EncryptedDocument`) rather than rebuilding this
#: string in a second language.
AAD_FORMAT = "manovia.share.v1.{token}.{document}"


@dataclass(frozen=True)
class EncryptedDocument:
    """One document as the viewer receives it: ciphertext, nonce, and how to read it.

    ``aad`` is not a secret — Associated Data is a binding, and the token is
    already in the URL. Sending it means the browser decrypts with the exact
    bytes the server authenticated, instead of holding a second copy of the
    format string that could silently disagree.
    """

    name: str
    ciphertext: str
    nonce: str
    algorithm: str
    content_type: str
    aad: str


@dataclass(frozen=True)
class CreatedShare:
    """The one and only response that carries the secret and the passcode."""

    share_id: uuid.UUID
    token: str
    secret: str
    passcode: str
    passcode_generated: bool
    share_path: str
    fragment: str
    expires_at: datetime
    view_limit: int
    formats: list[ShareFormat]
    items: list[ShareItem]
    payload_sha256: str
    consent_sha256: str
    consent_lines: list[str]
    document_bytes: dict[str, int]
    preview: SharePayload


@dataclass(frozen=True)
class PublicShare:
    """What an anonymous viewer gets after the passcode: ciphertext only."""

    share_id: uuid.UUID
    token_prefix: str
    generated_at: datetime
    expires_at: datetime
    views_remaining: int
    formats: list[str]
    items: list[str]
    algorithm: str
    key_derivation: str
    disclaimer_title: str
    disclaimer: str
    documents: list[EncryptedDocument] = field(default_factory=list)


@dataclass(frozen=True)
class CreateOptions:
    """The knobs on a create request, validated against :class:`Settings`."""

    formats: tuple[ShareFormat, ...] = (ShareFormat.PDF,)
    expires_in_days: int | None = None
    view_limit: int | None = None
    passcode: str | None = None
    consent_sha256: str | None = None


def consent_summary(payload: SharePayload) -> str:
    """The exact text a person confirms, as one string."""

    return "\n".join(consent_lines(payload))


def consent_hash(payload: SharePayload) -> str:
    """SHA-256 of :func:`consent_summary` — stored on the row, shown in the UI."""

    return hashlib.sha256(consent_summary(payload).encode("utf-8")).hexdigest()


def payload_hash(payload: SharePayload) -> str:
    """SHA-256 of the canonical JSON that gets encrypted."""

    return crypto.fingerprint(payload.as_json())


class ShareService:
    """Everything the API does with shares, in one place."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repo = ShareRepository(session)

    # -- the switch --------------------------------------------------------
    async def preference(self, user: User) -> SharePreference | None:
        """The person's sharing switch row, if they have one."""

        return await self._repo.preference(user.id)

    async def is_enabled(self, user: User) -> bool:
        """True only when both the deployment and the person allow sharing."""

        if not self._settings.share_enabled:
            return False
        row = await self._repo.preference(user.id)
        return bool(row is not None and row.enabled)

    async def set_enabled(self, user: User, *, enabled: bool) -> SharePreference:
        """Turn the person's switch on or off."""

        if enabled and not self._settings.share_enabled:
            raise SharingDisabledError("Sharing is turned off in this deployment.")
        return await self._repo.set_preference(user.id, enabled=enabled)

    async def require_enabled(self, user: User) -> None:
        """The gate every read-or-write of share content passes through."""

        if not self._settings.share_enabled:
            raise SharingDisabledError("Sharing is turned off in this deployment.")
        if not await self.is_enabled(user):
            raise SharingDisabledError(
                "Sharing with a professional is off. Turn it on to prepare a summary."
            )

    # -- catalogue ---------------------------------------------------------
    def catalogue(self) -> list[dict[str, Any]]:
        """The item list the builder renders, with the comprehension sentences."""

        return [
            {
                "key": item.key.value,
                "label": item.label,
                "blurb": item.blurb,
                "includes": list(item.includes),
                "excludes": list(item.excludes),
                "default": item.default,
                "needs_selection": item.needs_selection,
            }
            for item in ITEM_CATALOGUE
        ]

    def defaults(self) -> dict[str, Any]:
        """The settings-driven numbers the builder shows as defaults."""

        return {
            "enabled": self._settings.share_enabled,
            "default_ttl_days": self._settings.share_default_ttl_days,
            "min_ttl_days": self._settings.share_min_ttl_days,
            "max_ttl_days": self._settings.share_max_ttl_days,
            "default_view_limit": self._settings.share_default_view_limit,
            "max_view_limit": self._settings.share_max_view_limit,
            "max_active_shares": self._settings.share_max_active_per_user,
            "passcode_min_length": self._settings.share_passcode_min_length,
            "passcode_max_failures": self._settings.share_passcode_max_failures,
            "formats": [member.value for member in ShareFormat],
        }

    # -- preview and create ------------------------------------------------
    async def preview(self, user: User, request: ShareRequest) -> dict[str, Any]:
        """Exactly what would be shared, without storing anything.

        Returns the payload plus the consent text and its hash. The client shows
        the lines, and sends the hash back with the create request: that round
        trip is the comprehension check.
        """

        await self.require_enabled(user)
        try:
            payload = await self._build(user, request)
        except ShareContentError as exc:
            raise ShareRequestError(str(exc)) from exc
        return {
            "payload": payload,
            "consent_lines": consent_lines(payload),
            "consent_sha256": consent_hash(payload),
            "payload_sha256": payload_hash(payload),
        }

    async def _build(self, user: User, request: ShareRequest) -> SharePayload:
        """Build the payload, translating content errors into request errors."""

        return await build_payload(self._session, user, request)

    async def create(
        self, user: User, request: ShareRequest, options: CreateOptions
    ) -> CreatedShare:
        """Create one share. See the module docstring for the order of operations."""

        await self.require_enabled(user)
        if not options.formats:
            raise ShareRequestError("Choose at least one export format.")
        unknown = [member for member in options.formats if member not in set(ShareFormat)]
        if unknown:
            raise ShareRequestError(f"Unknown export format: {unknown[0]}")

        ttl = options.expires_in_days or self._settings.share_default_ttl_days
        if not self._settings.share_min_ttl_days <= ttl <= self._settings.share_max_ttl_days:
            raise ShareRequestError(
                f"The link must live between {self._settings.share_min_ttl_days} and "
                f"{self._settings.share_max_ttl_days} days."
            )
        limit = options.view_limit or self._settings.share_default_view_limit
        if not 1 <= limit <= self._settings.share_max_view_limit:
            raise ShareRequestError(
                f"The view limit must be between 1 and {self._settings.share_max_view_limit}."
            )

        active = await self._repo.count_active(user.id)
        if active >= self._settings.share_max_active_per_user:
            raise ShareLimitError(
                f"You have {active} live shares, which is the maximum. "
                "Revoke one (or all of them) first."
            )

        try:
            payload = await self._build(user, request)
        except ShareContentError as exc:
            raise ShareRequestError(str(exc)) from exc

        summary_hash = consent_hash(payload)
        if options.consent_sha256 is None:
            raise ConsentMismatchError(
                "Review the preview and confirm what will be shared before creating a link."
            )
        if not _same_hash(options.consent_sha256, summary_hash):
            raise ConsentMismatchError(
                "Your entries changed since you reviewed the preview. "
                "Look at the preview again and confirm it."
            )

        passcode, generated = self._passcode(options.passcode)
        share_id = uuid.uuid4()
        token = crypto.new_token()
        secret = crypto.new_secret()
        key = crypto.derive_key(secret=secret, passcode=passcode, token=token)

        documents: dict[str, bytes] = {"payload": payload.as_json()}
        if ShareFormat.PDF in options.formats:
            documents["pdf"] = render_pdf(payload)
        if ShareFormat.FHIR in options.formats:
            documents["fhir"] = bundle_bytes(payload, bundle_id=share_id)
        total = sum(len(value) for value in documents.values())
        if total > self._settings.share_max_document_bytes:
            raise ShareLimitError(
                "That selection makes too large a document. Share fewer items, or a shorter window."
            )

        encrypted: dict[str, tuple[bytes, bytes]] = {
            name: crypto.encrypt_document(key, blob, aad=share_aad(token, name))
            for name, blob in documents.items()
        }

        now = utcnow()
        salt = new_salt()
        share = ClinicianShare(
            id=share_id,
            user_id=user.id,
            token_hash=crypto.hash_token(token),
            token_prefix=token[:8],
            passcode_hash=hash_passcode(passcode, salt),
            passcode_salt=salt,
            passcode_failures=0,
            locked_until=None,
            expires_at=now + timedelta(days=ttl),
            revoked_at=None,
            view_limit=limit,
            view_count=0,
            formats=[member.value for member in ShareFormat if member in set(options.formats)],
            items=[member.value for member in payload.items],
            payload_ciphertext=encrypted["payload"][0],
            payload_nonce=encrypted["payload"][1],
            pdf_ciphertext=encrypted.get("pdf", (None, None))[0],
            pdf_nonce=encrypted.get("pdf", (None, None))[1],
            fhir_ciphertext=encrypted.get("fhir", (None, None))[0],
            fhir_nonce=encrypted.get("fhir", (None, None))[1],
            cipher=ShareCipher.A256GCM,
            consent_summary_sha256=summary_hash,
            created_at=now,
        )
        await self._repo.add(share)
        await self._repo.log(share.id, ShareEventKind.CREATED, actor=ACTOR_OWNER, now=now)

        # The secret is returned, not stored. Nothing after this line can read it
        # back, which is the whole point.
        secret_text = crypto.encode_bytes(secret)
        return CreatedShare(
            share_id=share.id,
            token=token,
            secret=secret_text,
            passcode=passcode,
            passcode_generated=generated,
            share_path=f"{SHARE_PATH_PREFIX}{token}",
            fragment=f"k={secret_text}",
            expires_at=share.expires_at,
            view_limit=share.view_limit,
            formats=share.format_list(),
            items=share.item_list(),
            payload_sha256=payload_hash(payload),
            consent_sha256=summary_hash,
            consent_lines=consent_lines(payload),
            document_bytes={name: len(blob) for name, blob in documents.items()},
            preview=payload,
        )

    def _passcode(self, supplied: str | None) -> tuple[str, bool]:
        """Validate a typed passcode, or generate one. Returns it and which happened."""

        if supplied is None or not supplied.strip():
            return generate_passcode(), True
        try:
            return validate(supplied), False
        except PasscodeError as exc:
            raise ShareRequestError(str(exc)) from exc

    # -- owner views -------------------------------------------------------
    async def list_shares(self, user: User) -> Sequence[ClinicianShare]:
        """Every share this person created, newest first."""

        return await self._repo.list_for_user(user.id)

    async def detail(self, user: User, share_id: uuid.UUID) -> ClinicianShare:
        """One of the person's own shares, or :class:`ShareNotFoundError`."""

        share = await self._repo.get(share_id)
        if share is None or share.user_id != user.id:
            raise ShareNotFoundError("No share with that id.")
        return share

    async def audit(
        self, user: User, share_id: uuid.UUID
    ) -> tuple[ClinicianShare, Sequence[ShareAccessEvent], dict[str, int]]:
        """The owner's view of one share's history: the row, the lines, the counts."""

        share = await self.detail(user, share_id)
        events = await self._repo.events(share.id)
        counts = await self._repo.event_counts(share.id)
        return share, events, counts

    async def revoke(self, user: User, share_id: uuid.UUID) -> ClinicianShare:
        """Revoke one share. Idempotent, and always audited the first time."""

        share = await self.detail(user, share_id)
        already = share.revoked_at is not None
        await self._repo.revoke(share)
        if not already:
            await self._repo.log(share.id, ShareEventKind.REVOKED, actor=ACTOR_OWNER)
        return share

    async def revoke_all(self, user: User) -> list[uuid.UUID]:
        """The one-click answer: every live share dies at once."""

        ids = await self._repo.revoke_all(user.id)
        for share_id in ids:
            await self._repo.log(share_id, ShareEventKind.REVOKED, actor=ACTOR_OWNER)
        return ids

    # -- public ------------------------------------------------------------
    async def public_status(self, token: str) -> dict[str, Any]:
        """What the viewer page may show *before* the passcode.

        Deliberately almost nothing: the banner, and that a passcode is needed.
        Anything unavailable — unknown, revoked, expired, locked, out of budget —
        raises the same :class:`ShareUnavailableError`.
        """

        share = await self._resolve(token, log_refusals=True)
        return {
            "available": True,
            "requires_passcode": True,
            "token_prefix": share.token_prefix,
            "formats": [member.value for member in share.format_list()],
            "disclaimer_title": DISCLAIMER_TITLE,
            "disclaimer": DISCLAIMER,
        }

    async def open_public(self, token: str, passcode: str) -> PublicShare:
        """Hand the ciphertext to a viewer, or refuse. See the module docstring."""

        share = await self._resolve(token, log_refusals=True)
        if share.is_locked:
            # ``as_utc`` because a database that stores naive timestamps (SQLite
            # in the tests) hands back a naive ``locked_until``.
            retry_after = (
                int((as_utc(share.locked_until) - utcnow()).total_seconds())
                if share.locked_until
                else self._settings.share_lockout_seconds
            )
            raise PasscodeLockedError(max(1, retry_after))

        candidate = (passcode or "").strip()
        if not verify_passcode(candidate, share.passcode_salt, share.passcode_hash):
            failures = int(share.passcode_failures) + 1
            lock = failures >= self._settings.share_passcode_max_failures
            locked_until = (
                utcnow() + timedelta(seconds=self._settings.share_lockout_seconds) if lock else None
            )
            await self._repo.register_passcode_failure(share, lock=lock, locked_until=locked_until)
            await self._repo.log(
                share.id,
                ShareEventKind.LOCKED if lock else ShareEventKind.PASSCODE_FAILED,
                actor=ACTOR_VIEWER,
            )
            if lock:
                raise PasscodeLockedError(self._settings.share_lockout_seconds)
            raise PasscodeIncorrectError(
                max(0, self._settings.share_passcode_max_failures - failures)
            )

        # The budget is claimed *after* the passcode verifies. Claiming it first
        # would let anybody who has the link but not the passcode burn the
        # views, and it would make ``view_count`` mean "attempts" instead of
        # "times the document was handed out". The claim is still one atomic
        # UPDATE, so two simultaneous correct opens hand out one document.
        if not await self._repo.claim_view(share.id):
            await self._repo.log(share.id, ShareEventKind.VIEW_LIMIT_REACHED, actor=ACTOR_VIEWER)
            raise ShareUnavailableError("This link has reached its view limit.")

        await self._repo.clear_passcode_failures(share)
        await self._repo.log(share.id, ShareEventKind.OPENED, actor=ACTOR_VIEWER)
        await self._session.refresh(share)

        documents = [
            EncryptedDocument(
                name=name,
                ciphertext=crypto.encode_bytes(ciphertext),
                nonce=crypto.encode_bytes(nonce),
                algorithm=share.cipher.value,
                content_type=CONTENT_TYPES[name],
                aad=share_aad(token, name).decode("utf-8"),
            )
            for name, ciphertext, nonce in self._stored_documents(share)
        ]
        return PublicShare(
            share_id=share.id,
            token_prefix=share.token_prefix,
            generated_at=share.created_at,
            expires_at=share.expires_at,
            views_remaining=share.views_remaining,
            formats=[member.value for member in share.format_list()],
            items=[member.value for member in share.item_list()],
            algorithm=share.cipher.value,
            key_derivation=KEY_DERIVATION,
            disclaimer_title=DISCLAIMER_TITLE,
            disclaimer=DISCLAIMER,
            documents=documents,
        )

    def _stored_documents(self, share: ClinicianShare) -> list[tuple[str, bytes, bytes]]:
        """The stored ciphertexts, in a fixed order, skipping empty slots."""

        found: list[tuple[str, bytes, bytes]] = [
            ("payload", share.payload_ciphertext, share.payload_nonce)
        ]
        if share.pdf_ciphertext is not None and share.pdf_nonce is not None:
            found.append(("pdf", share.pdf_ciphertext, share.pdf_nonce))
        if share.fhir_ciphertext is not None and share.fhir_nonce is not None:
            found.append(("fhir", share.fhir_ciphertext, share.fhir_nonce))
        return found

    async def _resolve(self, token: str, *, log_refusals: bool) -> ClinicianShare:
        """Find a usable share by token, or raise the one uniform refusal.

        ``log_refusals`` writes an audit line for the cases where a share exists
        (expired, revoked, out of budget). An unknown token cannot be logged —
        there is no row to log against — which is also why the public answer is
        identical in all four cases.
        """

        share = await self._repo.get_by_token(token) if token else None
        if share is None:
            raise ShareUnavailableError("This link is not available.")
        if share.is_revoked:
            if log_refusals:
                await self._repo.log(
                    share.id, ShareEventKind.ACCESS_AFTER_REVOKE, actor=ACTOR_VIEWER
                )
            raise ShareUnavailableError("This link is not available.")
        if share.is_expired:
            if log_refusals:
                await self._repo.log(share.id, ShareEventKind.EXPIRED, actor=ACTOR_VIEWER)
            raise ShareUnavailableError("This link is not available.")
        if share.views_remaining <= 0:
            if log_refusals:
                await self._repo.log(
                    share.id, ShareEventKind.VIEW_LIMIT_REACHED, actor=ACTOR_VIEWER
                )
            raise ShareUnavailableError("This link is not available.")
        return share


def _same_hash(left: str, right: str) -> bool:
    """Constant-time comparison of two hex digests."""

    return hmac.compare_digest(left.strip().lower(), right.strip().lower())


def documents_as_json(share: PublicShare) -> dict[str, Any]:
    """The wire form of :class:`PublicShare` (ciphertext strings, never bytes)."""

    return {
        "share_id": str(share.share_id),
        "token_prefix": share.token_prefix,
        "generated_at": share.generated_at.isoformat(),
        "expires_at": share.expires_at.isoformat(),
        "views_remaining": share.views_remaining,
        "formats": list(share.formats),
        "items": list(share.items),
        "algorithm": share.algorithm,
        "key_derivation": share.key_derivation,
        "disclaimer_title": share.disclaimer_title,
        "disclaimer": share.disclaimer,
        "documents": [
            {
                "name": document.name,
                "ciphertext": document.ciphertext,
                "nonce": document.nonce,
                "algorithm": document.algorithm,
                "content_type": document.content_type,
                "aad": document.aad,
            }
            for document in share.documents
        ],
    }


def parse_payload(raw: bytes) -> SharePayload:
    """Read a decrypted payload document back into a :class:`SharePayload`.

    Used by the tests and by the sample-bundle script; the browser parses the
    JSON itself and never needs this.
    """

    return SharePayload.model_validate(json.loads(raw.decode("utf-8")))


__all__ = [
    "AAD_FORMAT",
    "CONTENT_TYPES",
    "DISCLAIMER",
    "DISCLAIMER_TITLE",
    "KEY_DERIVATION",
    "SHARE_PATH_PREFIX",
    "ConsentMismatchError",
    "CreateOptions",
    "CreatedShare",
    "EncryptedDocument",
    "PasscodeIncorrectError",
    "PasscodeLockedError",
    "PublicShare",
    "ShareError",
    "ShareLimitError",
    "ShareNotFoundError",
    "ShareRequestError",
    "ShareService",
    "ShareUnavailableError",
    "SharingDisabledError",
    "consent_hash",
    "consent_summary",
    "documents_as_json",
    "parse_payload",
    "payload_hash",
]

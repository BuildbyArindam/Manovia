"""``clinician_shares`` — consented, time-limited, encrypted appointment summaries.

Day 47. One row is one share link a person created for a professional. Three
properties are structural, not policy:

* **The server stores ciphertext only.** The ``*_ciphertext`` columns hold
  AES-256-GCM output whose key is derived from a random secret returned to the
  owner *once*, in the URL fragment, and never written to this table, to a log or
  to any cache. There is deliberately no ``key`` column and no ``key_hash``
  column; ``tests/security/test_share_ciphertext_only.py`` fails if one appears.
* **The public handle is a hash.** ``token_hash`` is SHA-256 of the token in the
  link, so a leaked database does not become a pile of working links.
  ``token_prefix`` (8 characters) exists only so the owner can recognise a share
  in a list.
* **Expiry, revocation and a view budget are columns, not conventions.** Every
  read path checks all three before anything is handed back.

The audit log (:class:`ShareAccessEvent`) records *kinds* of events and nothing
else — no IP address, no user agent, no request body. The owner asked to see
"opened 2 times"; a surveillance log was not part of that deal.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    JSON_TYPE,
    TIMESTAMP_TYPE,
    Base,
    CreatedAtMixin,
    as_utc,
    enum_check,
    enum_type,
    utcnow,
)
from app.models.enums import SHARE_ITEM_ORDER, ShareCipher, ShareEventKind, ShareFormat, ShareItem

if TYPE_CHECKING:
    from app.models.user import User

#: Nonce size for AES-GCM (96 bits) and the size of the derived key.
SHARE_NONCE_BYTES = 12
SHARE_KEY_BYTES = 32

#: Characters of the public token kept for display in the owner's list.
TOKEN_PREFIX_LENGTH = 8


def share_aad(token: str, document: str) -> bytes:
    """Associated Data binding one ciphertext to one share *and* one document.

    GCM authenticates the AAD with the plaintext, so ciphertext copied out of
    another share — or the PDF slot pasted into the FHIR slot of the same share —
    fails to decrypt instead of rendering somebody else's document. The token is
    public (it is in the URL), which is fine: AAD is a binding, not a secret. One
    definition, as in :func:`app.models.cbt.cbt_aad`, because a writer and a
    reader that disagree about the spelling fail at read time, not at build time.
    """

    return f"manovia.share.v1.{token}.{document}".encode("utf-8")


class ClinicianShare(Base, CreatedAtMixin):
    """One share link: its budget, its expiry, and its ciphertext."""

    __tablename__ = "clinician_shares"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", name="fk_clinician_shares_user_id_users", ondelete="CASCADE"),
        nullable=False,
    )

    #: SHA-256 hex of the public token. One token, one row.
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: First characters of the token, for the owner's list only.
    token_prefix: Mapped[str] = mapped_column(sa.String(TOKEN_PREFIX_LENGTH), nullable=False)

    #: PBKDF2-HMAC-SHA256 over the passcode with a per-share salt. Never the
    #: passcode itself, and never a hash reusable anywhere else.
    passcode_hash: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    passcode_salt: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    #: Consecutive wrong passcodes; a successful open resets it.
    passcode_failures: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False, default=0)
    #: Set when failures reach the limit; the link refuses everyone until then.
    locked_until: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE)

    #: The window closes here. There is no such thing as a forever share.
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP_TYPE, nullable=False)
    #: One-click revoke (or "revoke everything") stamps this.
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE)

    #: How many times the ciphertext may be handed out, and how many times it was.
    view_limit: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    view_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)

    #: Selected :class:`~app.models.enums.ShareFormat` values.
    formats: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    #: Selected :class:`~app.models.enums.ShareItem` values. Category names only,
    #: never a value, so the owner can manage a share they can no longer open and
    #: nothing about the contents leaks from this table.
    items: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)

    #: The structured summary, the printable PDF and the FHIR bundle — each under
    #: its own nonce and its own AAD (see :func:`share_aad`).
    payload_ciphertext: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    payload_nonce: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    pdf_ciphertext: Mapped[bytes | None] = mapped_column(sa.LargeBinary())
    pdf_nonce: Mapped[bytes | None] = mapped_column(sa.LargeBinary())
    fhir_ciphertext: Mapped[bytes | None] = mapped_column(sa.LargeBinary())
    fhir_nonce: Mapped[bytes | None] = mapped_column(sa.LargeBinary())
    cipher: Mapped[ShareCipher] = mapped_column(enum_type(ShareCipher), nullable=False)

    #: SHA-256 of the exact consent summary the owner confirmed before creating
    #: the share: proof of *what* they agreed to, not a copy of it.
    consent_summary_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    user: Mapped[User] = relationship(back_populates="clinician_shares")
    events: Mapped[list[ShareAccessEvent]] = relationship(
        back_populates="share",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ShareAccessEvent.created_at",
    )

    __table_args__ = (
        sa.Index("ux_clinician_shares_token_hash", "token_hash", unique=True),
        sa.Index("ix_clinician_shares_user_id_created_at", "user_id", "created_at"),
        enum_check("cipher", ShareCipher),
        sa.CheckConstraint("view_limit >= 1", name="view_limit_at_least_one"),
        sa.CheckConstraint("view_count >= 0", name="view_count_not_negative"),
        sa.CheckConstraint("passcode_failures >= 0", name="passcode_failures_not_negative"),
    )

    @property
    def is_expired(self) -> bool:
        """True once ``expires_at`` is in the past."""

        return as_utc(self.expires_at) <= utcnow()

    @property
    def is_revoked(self) -> bool:
        """True once the owner has revoked this link."""

        return self.revoked_at is not None

    @property
    def is_locked(self) -> bool:
        """True while a passcode lockout is running."""

        return self.locked_until is not None and as_utc(self.locked_until) > utcnow()

    @property
    def views_remaining(self) -> int:
        """How many more opens the budget allows (never negative)."""

        return max(0, self.view_limit - self.view_count)

    @property
    def status(self) -> str:
        """One word for the UI: ``revoked`` / ``expired`` / ``locked`` / ``active``."""

        if self.is_revoked:
            return "revoked"
        if self.is_expired:
            return "expired"
        if self.is_locked:
            return "locked"
        return "active"

    def format_list(self) -> list[ShareFormat]:
        """The stored formats as enum members, in canonical order."""

        stored = {str(value) for value in (self.formats or [])}
        return [member for member in ShareFormat if member.value in stored]

    def item_list(self) -> list[ShareItem]:
        """The stored item keys as enum members, in canonical order."""

        stored = {str(value) for value in (self.items or [])}
        return [member for member in SHARE_ITEM_ORDER if member.value in stored]


class ShareAccessEvent(Base, CreatedAtMixin):
    """One line of the audit log the owner can read."""

    __tablename__ = "share_access_events"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    share_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey(
            "clinician_shares.id",
            name="fk_share_access_events_share_id_clinician_shares",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    kind: Mapped[ShareEventKind] = mapped_column(enum_type(ShareEventKind), nullable=False)
    #: ``owner`` / ``viewer`` / ``system``: who the event happened to, never who
    #: they were. No identifier, no address, no agent string.
    actor: Mapped[str] = mapped_column(sa.String(16), nullable=False)

    share: Mapped[ClinicianShare] = relationship(back_populates="events")

    __table_args__ = (
        sa.Index("ix_share_access_events_share_id_created_at", "share_id", "created_at"),
        enum_check("kind", ShareEventKind),
    )


class SharePreference(Base):
    """The per-person switch that makes sharing possible at all.

    Sharing is *off* until this row says otherwise, and the row is written only
    by an explicit action in the UI. No share can be previewed or created while
    it is off, so "off by default" is a database fact rather than a UI hint.
    """

    __tablename__ = "share_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", name="fk_share_preferences_user_id_users", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.false()
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, default=utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, default=utcnow, onupdate=utcnow
    )

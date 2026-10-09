"""Repositories: the only place raw queries live.

A repository takes an :class:`sqlalchemy.ext.asyncio.AsyncSession`, builds one
statement per method, and never commits — the caller owns the transaction (the
API dependency commits at the end of a successful request). Encryption and
decryption of the ``*_encrypted`` columns go through :mod:`app.core.crypto`, so
no caller has to remember to decrypt and no plaintext reaches the database.
"""

from app.db.repos.assessments import AssessmentRepository
from app.db.repos.chats import ChatRepository
from app.db.repos.consents import ConsentRepository
from app.db.repos.journals import JournalRepository
from app.db.repos.moods import MoodRepository
from app.db.repos.safety import SafetyEventRepository
from app.db.repos.users import UserRepository

__all__ = [
    "AssessmentRepository",
    "ChatRepository",
    "ConsentRepository",
    "JournalRepository",
    "MoodRepository",
    "SafetyEventRepository",
    "UserRepository",
]

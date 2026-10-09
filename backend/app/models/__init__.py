"""ORM models. Importing this package registers every table on ``Base.metadata``,
which is what Alembic autogenerate and the tests rely on.
"""

from app.models.assessment import AssessmentResult
from app.models.base import Base
from app.models.chat_session import ChatSession
from app.models.consent import Consent
from app.models.enums import (
    AssessmentBand,
    AssessmentInstrument,
    ConsentKind,
    MessageRole,
    RiskLevel,
    SafetyEventSource,
)
from app.models.journal import JournalEntry
from app.models.message import Message
from app.models.mood import MoodEntry
from app.models.refresh_token import RefreshToken
from app.models.safety_event import SafetyEvent
from app.models.user import User

__all__ = [
    "AssessmentBand",
    "AssessmentInstrument",
    "AssessmentResult",
    "Base",
    "ChatSession",
    "Consent",
    "ConsentKind",
    "JournalEntry",
    "Message",
    "MessageRole",
    "MoodEntry",
    "RefreshToken",
    "RiskLevel",
    "SafetyEvent",
    "SafetyEventSource",
    "User",
]

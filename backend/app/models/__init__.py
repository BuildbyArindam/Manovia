"""ORM models. Importing this package registers every table on ``Base.metadata``,
which is what Alembic autogenerate and the tests rely on.
"""

from app.models.assessment import AssessmentResult
from app.models.base import Base
from app.models.cbt import CBTRecord
from app.models.chat_session import ChatSession
from app.models.consent import Consent
from app.models.enums import (
    AssessmentBand,
    AssessmentInstrument,
    ConsentKind,
    MessageRole,
    PlanItemKind,
    PlanItemStatus,
    RiskLevel,
    SafetyEventCategory,
    SafetyEventSource,
)
from app.models.exercise_completion import ExerciseCompletion
from app.models.journal import JournalEntry
from app.models.message import Message
from app.models.mood import MoodEntry
from app.models.plan import PlanItem
from app.models.privacy import (
    RETENTION_DAY_CHOICES,
    RETENTION_MAX_DAYS,
    RETENTION_MIN_DAYS,
    DeletionReceipt,
    PrivacySetting,
)
from app.models.refresh_token import RefreshToken
from app.models.safety_event import SafetyEvent
from app.models.user import User
from app.models.user_settings import UserSettings

__all__ = [
    "RETENTION_DAY_CHOICES",
    "RETENTION_MAX_DAYS",
    "RETENTION_MIN_DAYS",
    "AssessmentBand",
    "AssessmentInstrument",
    "AssessmentResult",
    "Base",
    "CBTRecord",
    "ChatSession",
    "Consent",
    "ConsentKind",
    "DeletionReceipt",
    "ExerciseCompletion",
    "JournalEntry",
    "Message",
    "MessageRole",
    "MoodEntry",
    "PlanItem",
    "PlanItemKind",
    "PlanItemStatus",
    "PrivacySetting",
    "RefreshToken",
    "RiskLevel",
    "SafetyEvent",
    "SafetyEventCategory",
    "SafetyEventSource",
    "User",
    "UserSettings",
]

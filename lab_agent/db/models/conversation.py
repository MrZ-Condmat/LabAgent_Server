"""Persisted conversations owned by individual users."""

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lab_agent.db.base import Base
from lab_agent.db.models.user import utc_now

if TYPE_CHECKING:
    from .message import Message
    from .user import User


class ConversationType(str, Enum):
    """Conversation contexts currently supported by LabAgent."""

    OVERVIEW = "overview"
    ARXIV = "arxiv"
    JOURNAL = "journal"
    GENERAL = "general"
    DATABASE = "database"


class Conversation(Base):
    """A private conversation owned by exactly one user."""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "conversation_type IN "
            "('overview', 'arxiv', 'journal', 'general', 'database')",
            name="ck_conversations_type",
        ),
        Index("ix_conversations_user_id_updated_at", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="New conversation",
        server_default="New conversation",
    )
    conversation_type: Mapped[ConversationType] = mapped_column(
        SQLAlchemyEnum(
            ConversationType,
            name="conversation_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_class: [member.value for member in enum_class],
            length=16,
        ),
        nullable=False,
        default=ConversationType.GENERAL,
        server_default=ConversationType.GENERAL.value,
    )
    context_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.sequence_number",
    )

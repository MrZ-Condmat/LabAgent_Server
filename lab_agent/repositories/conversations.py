"""Ownership-safe data access for private conversations."""

from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from lab_agent.db.models import Conversation, ConversationType
from lab_agent.db.models.user import utc_now

from .errors import OwnedResourceNotFoundError


class ConversationRepository:
    """Manage conversations while keeping ownership in every private lookup."""

    def __init__(self, session: Session):
        self.session = session

    def create_for_user(
        self,
        user_id: UUID,
        *,
        title: str = "New conversation",
        conversation_type: ConversationType = ConversationType.GENERAL,
        context_metadata: dict[str, Any] | None = None,
    ) -> Conversation:
        conversation = Conversation(
            user_id=user_id,
            title=title,
            conversation_type=conversation_type,
            context_metadata=(
                dict(context_metadata) if context_metadata is not None else {}
            ),
        )
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def get_for_user(
        self,
        user_id: UUID,
        conversation_id: UUID,
    ) -> Conversation | None:
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
        return self.session.scalar(statement)

    def list_for_user(
        self,
        user_id: UUID,
        *,
        conversation_type: ConversationType | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Conversation]:
        statement = select(Conversation).where(Conversation.user_id == user_id)
        if conversation_type is not None:
            statement = statement.where(Conversation.conversation_type == conversation_type)
        if not include_archived:
            statement = statement.where(Conversation.archived_at.is_(None))
        statement = (
            statement.order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement).all())

    def rename_for_user(
        self,
        user_id: UUID,
        conversation_id: UUID,
        title: str,
    ) -> Conversation:
        conversation = self.get_for_user(user_id, conversation_id)
        if conversation is None:
            raise OwnedResourceNotFoundError("Conversation not found")
        conversation.title = title
        conversation.updated_at = utc_now()
        self.session.flush()
        return conversation

    def archive_for_user(
        self,
        user_id: UUID,
        conversation_id: UUID,
    ) -> Conversation:
        conversation = self.get_for_user(user_id, conversation_id)
        if conversation is None:
            raise OwnedResourceNotFoundError("Conversation not found")
        now = utc_now()
        conversation.archived_at = now
        conversation.updated_at = now
        self.session.flush()
        return conversation

    def delete_for_user(self, user_id: UUID, conversation_id: UUID) -> None:
        """Permanently remove one owned row; PostgreSQL cascades its messages."""
        statement = (
            delete(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .returning(Conversation.id)
        )
        if self.session.scalar(statement) is None:
            raise OwnedResourceNotFoundError("Conversation not found")

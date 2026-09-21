"""Ownership-safe access to ordered conversation messages."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lab_agent.db.models import Conversation, Message, MessageRole
from lab_agent.db.models.user import utc_now

from .errors import OwnedResourceNotFoundError


class MessageRepository:
    """Read and append messages through their owning conversation."""

    def __init__(self, session: Session):
        self.session = session

    def list_for_user_conversation(
        self,
        user_id: UUID,
        conversation_id: UUID,
    ) -> list[Message]:
        statement = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
            .order_by(Message.sequence_number.asc())
        )
        return list(self.session.scalars(statement).all())

    def append_message_for_user_conversation(
        self,
        user_id: UUID,
        conversation_id: UUID,
        *,
        role: MessageRole,
        content: str,
        metadata_json: dict[str, Any] | None = None,
    ) -> Message:
        # The row lock serializes appends for this conversation inside the caller's
        # transaction. MAX + 1 without this lock can allocate the same number twice.
        conversation_statement = (
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
            .with_for_update()
        )
        conversation = self.session.scalar(conversation_statement)
        if conversation is None:
            raise OwnedResourceNotFoundError("Conversation not found")

        sequence_statement = select(
            func.coalesce(func.max(Message.sequence_number), 0)
        ).where(Message.conversation_id == conversation_id)
        current_sequence = self.session.scalar(sequence_statement)
        next_sequence = int(current_sequence or 0) + 1

        message = Message(
            conversation_id=conversation_id,
            sequence_number=next_sequence,
            role=role,
            content=content,
            metadata_json=dict(metadata_json) if metadata_json is not None else {},
        )
        conversation.updated_at = utc_now()
        self.session.add(message)
        self.session.flush()
        return message

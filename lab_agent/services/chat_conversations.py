"""Private chat persistence with a short transaction per database operation."""

from __future__ import annotations

import re
from collections.abc import Callable
from uuid import UUID

from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import Conversation, ConversationType, Message, MessageRole
from lab_agent.db.session import SessionFactory, database_session
from lab_agent.repositories.conversations import ConversationRepository
from lab_agent.repositories.errors import OwnedResourceNotFoundError
from lab_agent.repositories.messages import MessageRepository


CHAT_TYPES = frozenset((ConversationType.OVERVIEW, ConversationType.ARXIV, ConversationType.JOURNAL))


def conversation_title(first_message: str) -> str:
    title = re.sub(r"\s+", " ", first_message).strip()
    return title[:97] + "..." if len(title) > 100 else title


class ChatConversationService:
    """Uses CurrentUser.id on every query; each call owns and closes its transaction."""

    def __init__(self, session_factory: SessionFactory | None = None):
        self.session_factory = session_factory

    @staticmethod
    def _type(conversation_type: ConversationType) -> None:
        if conversation_type not in CHAT_TYPES:
            raise ValueError("Unsupported chat type")

    def list_recent(
        self, current_user: CurrentUser, conversation_type: ConversationType, *, limit: int = 20
    ) -> list[Conversation]:
        self._type(conversation_type)
        with database_session(self.session_factory) as session:
            return ConversationRepository(session).list_for_user(
                current_user.id, conversation_type=conversation_type, limit=limit
            )

    def load(
        self, current_user: CurrentUser, conversation_type: ConversationType, conversation_id: UUID
    ) -> tuple[Conversation, list[Message]]:
        self._type(conversation_type)
        with database_session(self.session_factory) as session:
            conversation = ConversationRepository(session).get_for_user(current_user.id, conversation_id)
            if conversation is None or conversation.conversation_type != conversation_type:
                raise OwnedResourceNotFoundError("Conversation not found")
            messages = MessageRepository(session).list_for_user_conversation(current_user.id, conversation_id)
            return conversation, messages

    def append_user(
        self,
        current_user: CurrentUser,
        conversation_type: ConversationType,
        context: dict,
        content: str,
        conversation_id: UUID | None = None,
    ) -> UUID:
        self._type(conversation_type)
        if not content.strip():
            raise ValueError("Message cannot be empty")
        with database_session(self.session_factory) as session:
            conversations = ConversationRepository(session)
            if conversation_id is None:
                conversation = conversations.create_for_user(
                    current_user.id,
                    title=conversation_title(content),
                    conversation_type=conversation_type,
                    context_metadata=context,
                )
                conversation_id = conversation.id
            else:
                conversation = conversations.get_for_user(current_user.id, conversation_id)
                if conversation is None or conversation.conversation_type != conversation_type:
                    raise OwnedResourceNotFoundError("Conversation not found")
                if conversation.context_metadata != context:
                    raise ValueError("Conversation context has changed; start a new conversation")
            MessageRepository(session).append_message_for_user_conversation(
                current_user.id, conversation_id, role=MessageRole.USER, content=content
            )
            return conversation_id

    def append_assistant(
        self,
        current_user: CurrentUser,
        conversation_type: ConversationType,
        conversation_id: UUID,
        content: str,
    ) -> None:
        self._type(conversation_type)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Assistant response is empty")
        with database_session(self.session_factory) as session:
            conversation = ConversationRepository(session).get_for_user(current_user.id, conversation_id)
            if conversation is None or conversation.conversation_type != conversation_type:
                raise OwnedResourceNotFoundError("Conversation not found")
            MessageRepository(session).append_message_for_user_conversation(
                current_user.id, conversation_id, role=MessageRole.ASSISTANT, content=content
            )

    def delete_conversation(self, current_user: CurrentUser, conversation_id: UUID) -> None:
        """Commit a permanent, ownership-checked deletion in its own transaction."""
        with database_session(self.session_factory) as session:
            ConversationRepository(session).delete_for_user(current_user.id, conversation_id)

    def generate_reply(
        self,
        current_user: CurrentUser,
        conversation_type: ConversationType,
        conversation_id: UUID,
        generate: Callable[[list[Message]], str],
    ) -> str:
        """Call the model between two closed DB sessions, then persist only success."""
        _, messages = self.load(current_user, conversation_type, conversation_id)
        answer = generate(messages)
        self.append_assistant(current_user, conversation_type, conversation_id, answer)
        return answer


def model_history(messages: list[Message], *, limit: int = 20) -> list[dict[str, str]]:
    """Use only user-visible turns; system/context prompts are rebuilt at runtime."""
    return [
        {"role": message.role.value, "content": message.content}
        for message in messages[-limit:]
        if message.role in (MessageRole.USER, MessageRole.ASSISTANT)
    ]

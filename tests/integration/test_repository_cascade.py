"""Database-level ON DELETE CASCADE integration tests."""

import pytest
from sqlalchemy import delete, func, select

from lab_agent.db.models import Conversation, Message, MessageRole, User
from lab_agent.repositories import (
    ConversationRepository,
    MessageRepository,
    UserRepository,
)


pytestmark = pytest.mark.integration


def count_rows(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_deleting_user_cascades_to_conversation_and_messages(
    integration_session_factory,
):
    session = integration_session_factory()
    user = UserRepository(session).add(
        User(email="user-cascade@example.com", display_name="Cascade")
    )
    conversation = ConversationRepository(session).create_for_user(user.id)
    MessageRepository(session).append_message_for_user_conversation(
        user.id,
        conversation.id,
        role=MessageRole.USER,
        content="Cascade me",
    )
    session.commit()
    user_id = user.id
    session.close()

    delete_session = integration_session_factory()
    delete_session.execute(delete(User).where(User.id == user_id))
    delete_session.commit()
    assert count_rows(delete_session, User) == 0
    assert count_rows(delete_session, Conversation) == 0
    assert count_rows(delete_session, Message) == 0
    delete_session.close()


def test_deleting_conversation_cascades_to_messages(
    integration_session_factory,
):
    session = integration_session_factory()
    user = UserRepository(session).add(
        User(email="conversation-cascade@example.com", display_name="Cascade")
    )
    conversation = ConversationRepository(session).create_for_user(user.id)
    MessageRepository(session).append_message_for_user_conversation(
        user.id,
        conversation.id,
        role=MessageRole.USER,
        content="Cascade me",
    )
    session.commit()
    conversation_id = conversation.id
    session.close()

    delete_session = integration_session_factory()
    delete_session.execute(
        delete(Conversation).where(Conversation.id == conversation_id)
    )
    delete_session.commit()
    assert count_rows(delete_session, User) == 1
    assert count_rows(delete_session, Conversation) == 0
    assert count_rows(delete_session, Message) == 0
    delete_session.close()

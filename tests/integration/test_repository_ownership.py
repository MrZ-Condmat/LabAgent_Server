"""Repository ownership, ordering, archive, and transaction integration tests."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from lab_agent.db.models import Conversation, ConversationType, MessageRole, User
from lab_agent.db.session import database_session
from lab_agent.repositories import (
    ConversationRepository,
    MessageRepository,
    OwnedResourceNotFoundError,
    UserRepository,
)


pytestmark = pytest.mark.integration


def add_user(session, email: str) -> User:
    return UserRepository(session).add(User(email=email, display_name=email))


def test_cross_user_conversation_and_message_access_is_hidden(db_session):
    user_a = add_user(db_session, "owner-a@example.com")
    user_b = add_user(db_session, "owner-b@example.com")
    conversations = ConversationRepository(db_session)
    conversation_a = conversations.create_for_user(user_a.id, title="A")
    conversation_b = conversations.create_for_user(user_b.id, title="B")
    messages = MessageRepository(db_session)
    messages.append_message_for_user_conversation(
        user_b.id,
        conversation_b.id,
        role=MessageRole.USER,
        content="Private B message",
    )
    db_session.commit()

    assert conversations.get_for_user(user_a.id, conversation_a.id) is conversation_a
    assert conversations.get_for_user(user_a.id, conversation_b.id) is None
    assert messages.list_for_user_conversation(user_a.id, conversation_b.id) == []
    with pytest.raises(
        OwnedResourceNotFoundError,
        match="^Conversation not found$",
    ):
        conversations.rename_for_user(user_a.id, conversation_b.id, "Leaked")
    with pytest.raises(
        OwnedResourceNotFoundError,
        match="^Conversation not found$",
    ):
        messages.append_message_for_user_conversation(
            user_a.id,
            conversation_b.id,
            role=MessageRole.USER,
            content="Cross-user write",
        )


def test_archive_and_stable_conversation_order(db_session):
    user = add_user(db_session, "ordering@example.com")
    base_time = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
    conversations = [
        Conversation(
            id=UUID(int=index),
            user_id=user.id,
            title=f"Conversation {index}",
            conversation_type=ConversationType.GENERAL,
            context_metadata={},
            created_at=base_time,
            updated_at=base_time + timedelta(hours=1 if index == 3 else 0),
        )
        for index in (1, 2, 3)
    ]
    db_session.add_all(conversations)
    db_session.commit()
    repository = ConversationRepository(db_session)

    assert [item.id for item in repository.list_for_user(user.id)] == [
        UUID(int=3),
        UUID(int=2),
        UUID(int=1),
    ]

    repository.archive_for_user(user.id, UUID(int=3))
    db_session.commit()
    assert UUID(int=3) not in {
        item.id for item in repository.list_for_user(user.id)
    }
    assert UUID(int=3) in {
        item.id
        for item in repository.list_for_user(user.id, include_archived=True)
    }


def test_message_sequence_and_read_order(db_session):
    user = add_user(db_session, "sequence@example.com")
    conversation = ConversationRepository(db_session).create_for_user(user.id)
    repository = MessageRepository(db_session)

    roles = [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    for index, role in enumerate(roles):
        repository.append_message_for_user_conversation(
            user.id,
            conversation.id,
            role=role,
            content=f"Message {index}",
        )
    db_session.commit()

    messages = repository.list_for_user_conversation(user.id, conversation.id)
    assert [message.sequence_number for message in messages] == [1, 2, 3, 4]


def test_repository_flush_is_invisible_until_caller_commit(
    integration_session_factory,
):
    setup_session = integration_session_factory()
    user = add_user(setup_session, "visibility@example.com")
    setup_session.commit()
    user_id = user.id
    setup_session.close()

    writer = integration_session_factory()
    reader = integration_session_factory()
    try:
        conversation = ConversationRepository(writer).create_for_user(user_id)
        conversation_id = conversation.id
        assert ConversationRepository(reader).get_for_user(
            user_id, conversation_id
        ) is None

        writer.commit()
        assert ConversationRepository(reader).get_for_user(
            user_id, conversation_id
        ) is not None
    finally:
        writer.rollback()
        reader.rollback()
        writer.close()
        reader.close()


def test_database_session_rolls_back_on_exception(integration_session_factory):
    email = "rollback@example.com"
    with pytest.raises(RuntimeError, match="force rollback"):
        with database_session(integration_session_factory) as session:
            UserRepository(session).add(User(email=email, display_name="Rollback"))
            raise RuntimeError("force rollback")

    verification_session = integration_session_factory()
    try:
        assert verification_session.scalar(
            select(User).where(User.email == email)
        ) is None
    finally:
        verification_session.close()

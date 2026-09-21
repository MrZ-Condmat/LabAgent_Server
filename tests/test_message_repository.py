from datetime import timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from lab_agent.db.models import Conversation, MessageRole
from lab_agent.repositories import MessageRepository, OwnedResourceNotFoundError


def compile_postgresql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_message_list_joins_conversation_and_filters_owner():
    session = Mock(spec=Session)
    scalar_result = Mock()
    scalar_result.all.return_value = []
    session.scalars.return_value = scalar_result
    repository = MessageRepository(session)
    user_id = uuid4()
    conversation_id = uuid4()

    assert repository.list_for_user_conversation(user_id, conversation_id) == []

    sql = compile_postgresql(session.scalars.call_args.args[0])
    assert "JOIN conversations" in sql
    assert "conversations.id" in sql
    assert "conversations.user_id" in sql
    assert str(conversation_id) in sql
    assert str(user_id) in sql
    assert "ORDER BY messages.sequence_number ASC" in sql


def test_append_locks_owner_then_allocates_next_sequence_and_flushes():
    session = Mock(spec=Session)
    conversation = Conversation(user_id=uuid4(), title="Discussion")
    session.scalar.side_effect = [conversation, 5]
    repository = MessageRepository(session)
    user_id = uuid4()
    conversation_id = uuid4()
    supplied_metadata = {"model": "test-model"}

    message = repository.append_message_for_user_conversation(
        user_id,
        conversation_id,
        role=MessageRole.ASSISTANT,
        content="Answer",
        metadata_json=supplied_metadata,
    )

    assert session.scalar.call_count == 2
    lock_sql = compile_postgresql(session.scalar.call_args_list[0].args[0])
    sequence_sql = compile_postgresql(session.scalar.call_args_list[1].args[0])
    assert "conversations.id" in lock_sql
    assert "conversations.user_id" in lock_sql
    assert str(conversation_id) in lock_sql
    assert str(user_id) in lock_sql
    assert lock_sql.rstrip().endswith("FOR UPDATE")
    assert "max(messages.sequence_number)" in sequence_sql.lower()
    assert "messages.conversation_id" in sequence_sql
    assert str(conversation_id) in sequence_sql

    assert message.conversation_id == conversation_id
    assert message.sequence_number == 6
    assert message.role is MessageRole.ASSISTANT
    assert message.content == "Answer"
    assert message.metadata_json == supplied_metadata
    assert message.metadata_json is not supplied_metadata
    assert conversation.updated_at.tzinfo is timezone.utc
    session.add.assert_called_once_with(message)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_append_for_cross_user_or_missing_conversation_hides_existence():
    session = Mock(spec=Session)
    session.scalar.return_value = None
    repository = MessageRepository(session)

    with pytest.raises(
        OwnedResourceNotFoundError,
        match="^Conversation not found$",
    ):
        repository.append_message_for_user_conversation(
            uuid4(),
            uuid4(),
            role=MessageRole.USER,
            content="Private request",
        )

    assert session.scalar.call_count == 1
    lock_sql = compile_postgresql(session.scalar.call_args.args[0])
    assert "conversations.user_id" in lock_sql
    assert lock_sql.rstrip().endswith("FOR UPDATE")
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()

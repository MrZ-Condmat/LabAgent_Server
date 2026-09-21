from datetime import timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from lab_agent.db.models import Conversation, ConversationType
from lab_agent.repositories import (
    ConversationRepository,
    OwnedResourceNotFoundError,
)


def compile_postgresql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_create_for_user_binds_owner_and_flushes_without_commit():
    session = Mock(spec=Session)
    repository = ConversationRepository(session)
    user_id = uuid4()
    supplied_metadata = {"report_date": "2026-09-21"}

    conversation = repository.create_for_user(
        user_id,
        title="Daily report",
        conversation_type=ConversationType.ARXIV,
        context_metadata=supplied_metadata,
    )

    assert conversation.user_id == user_id
    assert conversation.title == "Daily report"
    assert conversation.conversation_type is ConversationType.ARXIV
    assert conversation.context_metadata == supplied_metadata
    assert conversation.context_metadata is not supplied_metadata
    session.add.assert_called_once_with(conversation)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()


def test_get_for_user_filters_owner_and_id_in_the_same_query():
    session = Mock(spec=Session)
    session.scalar.return_value = None
    repository = ConversationRepository(session)
    user_id = uuid4()
    conversation_id = uuid4()

    assert repository.get_for_user(user_id, conversation_id) is None

    sql = compile_postgresql(session.scalar.call_args.args[0])
    assert "conversations.id" in sql
    assert "conversations.user_id" in sql
    assert str(conversation_id) in sql
    assert str(user_id) in sql


def test_list_for_user_excludes_archived_and_has_stable_recent_order():
    session = Mock(spec=Session)
    scalar_result = Mock()
    scalar_result.all.return_value = []
    session.scalars.return_value = scalar_result
    repository = ConversationRepository(session)
    user_id = uuid4()

    assert repository.list_for_user(user_id, limit=20, offset=10) == []

    sql = compile_postgresql(session.scalars.call_args.args[0])
    assert "conversations.user_id" in sql
    assert str(user_id) in sql
    assert "conversations.archived_at IS NULL" in sql
    assert "ORDER BY conversations.updated_at DESC, conversations.id DESC" in sql
    assert "LIMIT 20 OFFSET 10" in sql


def test_list_for_user_can_include_archived_without_dropping_owner_filter():
    session = Mock(spec=Session)
    scalar_result = Mock()
    scalar_result.all.return_value = []
    session.scalars.return_value = scalar_result
    repository = ConversationRepository(session)
    user_id = uuid4()

    repository.list_for_user(user_id, include_archived=True)

    sql = compile_postgresql(session.scalars.call_args.args[0])
    assert "conversations.user_id" in sql
    assert str(user_id) in sql
    assert "archived_at IS NULL" not in sql


@pytest.mark.parametrize("method_name", ["rename_for_user", "archive_for_user"])
def test_cross_user_or_missing_mutation_has_same_not_found_error(method_name):
    session = Mock(spec=Session)
    session.scalar.return_value = None
    repository = ConversationRepository(session)
    arguments = [uuid4(), uuid4()]
    if method_name == "rename_for_user":
        arguments.append("Hidden title")

    with pytest.raises(
        OwnedResourceNotFoundError,
        match="^Conversation not found$",
    ):
        getattr(repository, method_name)(*arguments)

    sql = compile_postgresql(session.scalar.call_args.args[0])
    assert "conversations.id" in sql
    assert "conversations.user_id" in sql
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_rename_and_archive_update_owned_conversation_without_commit():
    session = Mock(spec=Session)
    conversation = Conversation(user_id=uuid4(), title="Old title")
    session.scalar.return_value = conversation
    repository = ConversationRepository(session)
    user_id = uuid4()
    conversation_id = uuid4()

    renamed = repository.rename_for_user(user_id, conversation_id, "New title")
    assert renamed.title == "New title"
    assert renamed.updated_at.tzinfo is timezone.utc

    archived = repository.archive_for_user(user_id, conversation_id)
    assert archived.archived_at is not None
    assert archived.archived_at == archived.updated_at
    assert archived.archived_at.tzinfo is timezone.utc
    assert session.flush.call_count == 2
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()

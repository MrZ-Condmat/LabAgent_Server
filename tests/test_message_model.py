import uuid

from sqlalchemy import CheckConstraint, Enum, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import configure_mappers

from lab_agent.db.base import Base
from lab_agent.db.models import (
    Conversation,
    ConversationType,
    Message,
    MessageRole,
    User,
)


def test_message_model_registers_expected_columns():
    table = Message.__table__

    assert Base.metadata.tables["messages"] is table
    assert list(table.columns.keys()) == [
        "id",
        "conversation_id",
        "sequence_number",
        "role",
        "content",
        "metadata_json",
        "created_at",
    ]
    assert isinstance(table.c.id.type, Uuid)
    assert table.c.id.primary_key is True
    assert table.c.id.nullable is False
    assert isinstance(table.c.id.default.arg(None), uuid.UUID)
    assert table.c.conversation_id.nullable is False
    assert table.c.sequence_number.nullable is False
    assert isinstance(table.c.content.type, Text)
    assert table.c.content.nullable is False
    assert isinstance(table.c.metadata_json.type, JSONB)
    assert table.c.metadata_json.nullable is False
    assert callable(table.c.metadata_json.default.arg)
    assert str(table.c.metadata_json.server_default.arg) == "'{}'::jsonb"
    assert table.c.created_at.type.timezone is True
    assert table.c.created_at.server_default is not None


def test_message_ownership_role_and_sequence_constraint():
    table = Message.__table__
    foreign_key = next(iter(table.c.conversation_id.foreign_keys))
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    unique_constraints = {
        constraint.name: [column.name for column in constraint.columns]
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert foreign_key.target_fullname == "conversations.id"
    assert foreign_key.ondelete == "CASCADE"
    assert isinstance(table.c.role.type, Enum)
    assert table.c.role.type.native_enum is False
    assert table.c.role.type.enums == ["user", "assistant", "system", "tool"]
    assert "ck_messages_role" in checks
    assert unique_constraints == {
        "uq_messages_conversation_sequence": [
            "conversation_id",
            "sequence_number",
        ]
    }
    assert Message.conversation.property.back_populates == "messages"
    assert "delete-orphan" in Conversation.messages.property.cascade
    assert Conversation.messages.property.passive_deletes is True


def test_relationships_configure_and_link_plain_python_objects():
    configure_mappers()

    user = User(email="researcher@example.com", display_name="Researcher")
    conversation = Conversation(
        title="Paper discussion",
        conversation_type=ConversationType.ARXIV,
    )
    message = Message(
        sequence_number=1,
        role=MessageRole.USER,
        content="Summarize this paper.",
    )
    user.conversations.append(conversation)
    conversation.messages.append(message)

    assert conversation.user is user
    assert message.conversation is conversation
    assert message not in user.__dict__.get("messages", [])

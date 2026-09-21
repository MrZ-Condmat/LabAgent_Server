import uuid

from sqlalchemy import CheckConstraint, Enum, Index, Uuid
from sqlalchemy.dialects.postgresql import JSONB

from lab_agent.db.base import Base
from lab_agent.db.models import Conversation, ConversationType, User


def test_conversation_model_registers_expected_columns():
    table = Conversation.__table__

    assert Base.metadata.tables["conversations"] is table
    assert list(table.columns.keys()) == [
        "id",
        "user_id",
        "title",
        "conversation_type",
        "context_metadata",
        "created_at",
        "updated_at",
        "archived_at",
    ]
    assert isinstance(table.c.id.type, Uuid)
    assert table.c.id.primary_key is True
    assert table.c.id.nullable is False
    assert isinstance(table.c.id.default.arg(None), uuid.UUID)
    assert table.c.user_id.nullable is False
    assert table.c.title.nullable is False
    assert table.c.title.type.length == 255
    assert table.c.context_metadata.nullable is False
    assert isinstance(table.c.context_metadata.type, JSONB)
    assert callable(table.c.context_metadata.default.arg)
    assert str(table.c.context_metadata.server_default.arg) == "'{}'::jsonb"
    assert table.c.archived_at.nullable is True


def test_conversation_ownership_type_and_lookup_index():
    table = Conversation.__table__
    foreign_key = next(iter(table.c.user_id.foreign_keys))
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    indexes = {index.name: index for index in table.indexes if isinstance(index, Index)}

    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "CASCADE"
    assert isinstance(table.c.conversation_type.type, Enum)
    assert table.c.conversation_type.type.native_enum is False
    assert table.c.conversation_type.type.enums == [
        "overview",
        "arxiv",
        "journal",
        "general",
        "database",
    ]
    assert table.c.conversation_type.default.arg is ConversationType.GENERAL
    assert str(table.c.conversation_type.server_default.arg) == "general"
    assert "ck_conversations_type" in checks
    lookup_index = indexes["ix_conversations_user_id_updated_at"]
    assert [column.name for column in lookup_index.columns] == ["user_id", "updated_at"]
    assert lookup_index.unique is False


def test_conversation_timestamps_and_relationships():
    table = Conversation.__table__

    for name in ("created_at", "updated_at", "archived_at"):
        assert table.c[name].type.timezone is True
    assert table.c.created_at.default is not None
    assert table.c.created_at.server_default is not None
    assert table.c.updated_at.default is not None
    assert table.c.updated_at.onupdate is not None
    assert table.c.updated_at.server_default is not None

    assert Conversation.user.property.back_populates == "conversations"
    assert Conversation.messages.property.back_populates == "conversation"
    assert User.conversations.property.back_populates == "user"
    assert "delete-orphan" in User.conversations.property.cascade
    assert User.conversations.property.passive_deletes is True

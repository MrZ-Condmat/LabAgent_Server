from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Column, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0002_create_conversations_messages.py"
)


def load_migration():
    spec = spec_from_file_location("conversation_message_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_extends_users_revision():
    migration = load_migration()

    assert migration.revision == "0002_conversations_messages"
    assert migration.down_revision == "0001_create_users"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


def test_migration_operations_follow_dependency_order():
    migration = load_migration()
    calls = []
    migration.op = SimpleNamespace(
        create_table=lambda name, *items, **kwargs: calls.append(
            ("create_table", name, items, kwargs)
        ),
        create_index=lambda name, table, columns, **kwargs: calls.append(
            ("create_index", name, table, columns, kwargs)
        ),
        drop_table=lambda name: calls.append(("drop_table", name)),
        drop_index=lambda name, **kwargs: calls.append(("drop_index", name, kwargs)),
    )

    migration.upgrade()
    migration.downgrade()

    assert [(call[0], call[1]) for call in calls] == [
        ("create_table", "conversations"),
        ("create_index", "ix_conversations_user_id_updated_at"),
        ("create_table", "messages"),
        ("drop_table", "messages"),
        ("drop_index", "ix_conversations_user_id_updated_at"),
        ("drop_table", "conversations"),
    ]

    conversation_items = calls[0][2]
    message_items = calls[2][2]
    conversation_columns = {
        item.name: item for item in conversation_items if isinstance(item, Column)
    }
    message_columns = {
        item.name: item for item in message_items if isinstance(item, Column)
    }
    conversation_foreign_key = next(
        item for item in conversation_items if isinstance(item, ForeignKeyConstraint)
    )
    message_foreign_key = next(
        item for item in message_items if isinstance(item, ForeignKeyConstraint)
    )

    assert isinstance(conversation_columns["context_metadata"].type, JSONB)
    assert isinstance(message_columns["metadata_json"].type, JSONB)
    assert list(conversation_foreign_key.elements)[0].target_fullname == "users.id"
    assert conversation_foreign_key.ondelete == "CASCADE"
    assert list(message_foreign_key.elements)[0].target_fullname == "conversations.id"
    assert message_foreign_key.ondelete == "CASCADE"
    assert any(
        isinstance(item, UniqueConstraint)
        and item.name == "uq_messages_conversation_sequence"
        for item in message_items
    )


def test_offline_sql_builds_complete_chain_without_altering_users(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://migration:placeholder@localhost:5432/labagent",
    )
    config = AlembicConfig("alembic.ini")
    config.output_buffer = StringIO()

    command.upgrade(config, "head", sql=True)
    sql = config.output_buffer.getvalue().lower()

    users_position = sql.index("create table users")
    conversations_position = sql.index("create table conversations")
    messages_position = sql.index("create table messages")
    assert users_position < conversations_position < messages_position
    assert "foreign key(user_id) references users (id) on delete cascade" in sql
    assert (
        "foreign key(conversation_id) references conversations (id) "
        "on delete cascade"
    ) in sql
    assert "context_metadata jsonb" in sql
    assert "metadata_json jsonb" in sql
    assert "alter table users" not in sql

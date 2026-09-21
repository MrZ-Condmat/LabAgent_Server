from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Column, Uuid


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0003_add_entra_identity_fields.py"
)


def load_migration():
    spec = spec_from_file_location("entra_identity_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_entra_migration_extends_conversation_message_revision():
    migration = load_migration()

    assert migration.revision == "0003_entra_identity"
    assert migration.down_revision == "0002_conversations_messages"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


def test_entra_migration_adds_and_removes_only_identity_fields():
    migration = load_migration()
    calls = []
    migration.op = SimpleNamespace(
        add_column=lambda table, column: calls.append(("add_column", table, column)),
        create_unique_constraint=lambda name, table, columns: calls.append(
            ("create_unique", name, table, columns)
        ),
        drop_constraint=lambda name, table, **kwargs: calls.append(
            ("drop_constraint", name, table, kwargs)
        ),
        drop_column=lambda table, column: calls.append(
            ("drop_column", table, column)
        ),
    )

    migration.upgrade()
    migration.downgrade()

    assert [(call[0], call[1]) for call in calls] == [
        ("add_column", "users"),
        ("add_column", "users"),
        ("create_unique", "uq_users_tenant_object"),
        ("drop_constraint", "uq_users_tenant_object"),
        ("drop_column", "users"),
        ("drop_column", "users"),
    ]
    added_columns = [call[2] for call in calls[:2]]
    assert all(isinstance(column, Column) for column in added_columns)
    assert [column.name for column in added_columns] == [
        "tenant_id",
        "external_object_id",
    ]
    assert all(isinstance(column.type, Uuid) for column in added_columns)
    assert all(column.nullable is True for column in added_columns)
    assert calls[2][2:] == (
        "users",
        ["tenant_id", "external_object_id"],
    )
    assert calls[3][3] == {"type_": "unique"}


def test_offline_sql_contains_entra_columns_and_composite_unique(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://migration:placeholder@localhost:5432/labagent",
    )
    config = AlembicConfig("alembic.ini")
    config.output_buffer = StringIO()

    command.upgrade(config, "head", sql=True)
    sql = " ".join(config.output_buffer.getvalue().lower().split())

    assert "alter table users add column tenant_id uuid" in sql
    assert "alter table users add column external_object_id uuid" in sql
    assert (
        "constraint uq_users_tenant_object unique "
        "(tenant_id, external_object_id)"
    ) in sql
    assert "alter table conversations" not in sql
    assert "alter table messages" not in sql

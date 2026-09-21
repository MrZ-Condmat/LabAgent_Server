from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import CheckConstraint, Column, UniqueConstraint


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0001_create_users_table.py"
)


def load_migration():
    spec = spec_from_file_location("create_users_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_user_migration_has_single_root_revision():
    migration = load_migration()

    assert migration.revision == "0001_create_users"
    assert migration.down_revision is None
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


def test_user_migration_upgrade_and_downgrade_are_scoped_to_users():
    migration = load_migration()
    calls = []
    migration.op = SimpleNamespace(
        create_table=lambda name, *items, **kwargs: calls.append(
            ("create", name, items, kwargs)
        ),
        drop_table=lambda name: calls.append(("drop", name)),
    )

    migration.upgrade()
    migration.downgrade()

    assert calls[0][0:2] == ("create", "users")
    columns = [item for item in calls[0][2] if isinstance(item, Column)]
    assert [column.name for column in columns] == [
        "id",
        "external_subject",
        "email",
        "display_name",
        "role",
        "is_active",
        "created_at",
        "updated_at",
        "last_login_at",
    ]
    assert {
        item.name
        for item in calls[0][2]
        if isinstance(item, UniqueConstraint)
    } == {"uq_users_email", "uq_users_external_subject"}
    assert {
        item.name
        for item in calls[0][2]
        if isinstance(item, CheckConstraint)
    } == {"ck_users_role"}
    assert calls[1] == ("drop", "users")


def test_offline_upgrade_renders_only_user_business_table(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://migration:placeholder@localhost:5432/labagent",
    )
    config = AlembicConfig("alembic.ini")
    config.output_buffer = StringIO()

    command.upgrade(config, "head", sql=True)
    sql = config.output_buffer.getvalue().lower()

    assert "create table users" in sql
    assert "ck_users_role" in sql
    assert "uq_users_email" in sql
    assert "uq_users_external_subject" in sql
    for unrelated_table in ("conversations", "messages", "reports", "sessions"):
        assert f"create table {unrelated_table}" not in sql

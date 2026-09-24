"""Real PostgreSQL migration and reflected-schema checks."""

import pytest
from sqlalchemy import Boolean, DateTime, Integer, String, Text, inspect, text
from sqlalchemy.dialects.postgresql import JSONB, UUID


pytestmark = pytest.mark.integration


def columns_by_name(inspector, table_name):
    return {column["name"]: column for column in inspector.get_columns(table_name)}


def test_upgrade_downgrade_upgrade_cycle_and_revision(
    integration_engine,
    migration_cycle,
):
    assert {"users", "conversations", "messages", "email_login_challenges", "user_sessions"}.isdisjoint(
        migration_cycle.tables_after_downgrade
    )
    assert migration_cycle.enum_types_after_downgrade == frozenset()

    inspector = inspect(integration_engine)
    assert {"users", "conversations", "messages", "email_login_challenges", "user_sessions", "alembic_version"}.issubset(
        set(inspector.get_table_names())
    )
    with integration_engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert revision == "0004_email_otp_auth_foundation"


def test_reflected_postgresql_column_types(integration_engine):
    inspector = inspect(integration_engine)
    users = columns_by_name(inspector, "users")
    conversations = columns_by_name(inspector, "conversations")
    messages = columns_by_name(inspector, "messages")
    challenges = columns_by_name(inspector, "email_login_challenges")
    sessions = columns_by_name(inspector, "user_sessions")

    assert isinstance(users["id"]["type"], UUID)
    assert isinstance(users["tenant_id"]["type"], UUID)
    assert isinstance(users["external_object_id"]["type"], UUID)
    assert users["tenant_id"]["nullable"] is True
    assert users["external_object_id"]["nullable"] is True
    assert isinstance(users["email"]["type"], String)
    assert isinstance(users["is_active"]["type"], Boolean)
    assert isinstance(users["created_at"]["type"], DateTime)
    assert users["created_at"]["type"].timezone is True

    assert isinstance(conversations["id"]["type"], UUID)
    assert isinstance(conversations["context_metadata"]["type"], JSONB)
    assert isinstance(conversations["updated_at"]["type"], DateTime)
    assert conversations["updated_at"]["type"].timezone is True

    assert isinstance(messages["id"]["type"], UUID)
    assert isinstance(messages["metadata_json"]["type"], JSONB)
    assert isinstance(messages["content"]["type"], Text)
    assert isinstance(messages["sequence_number"]["type"], Integer)
    assert isinstance(messages["created_at"]["type"], DateTime)
    assert messages["created_at"]["type"].timezone is True
    assert isinstance(challenges["id"]["type"], UUID)
    assert challenges["expires_at"]["type"].timezone is True
    assert sessions["expires_at"]["type"].timezone is True

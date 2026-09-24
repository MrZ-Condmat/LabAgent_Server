"""Fixtures for destructive tests against a disposable PostgreSQL database."""

from dataclasses import dataclass
import os
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker


PROJECT_ROOT = Path(__file__).parents[2]
PROTECTED_DATABASE_NAMES = {"postgres", "template0", "template1", "labagent"}
BUSINESS_TABLES = {"users", "conversations", "messages", "email_login_challenges", "user_sessions"}


@dataclass(frozen=True)
class MigrationCycleResult:
    tables_after_downgrade: frozenset[str]
    enum_types_after_downgrade: frozenset[str]


def validate_test_database_url(raw_url: str) -> str:
    """Reject URLs that do not clearly identify a disposable test database."""
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("TEST_DATABASE_URL must use PostgreSQL")

    database_name = (url.database or "").lower()
    if database_name in PROTECTED_DATABASE_NAMES:
        raise RuntimeError("TEST_DATABASE_URL points to a protected database name")
    if not any(marker in database_name for marker in ("test", "integration")):
        raise RuntimeError(
            "TEST_DATABASE_URL database name must contain 'test' or 'integration'"
        )
    return raw_url


def alembic_config(database_url: str) -> AlembicConfig:
    """Inject the validated test URL without using DATABASE_URL."""
    config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.attributes["database_url"] = database_url
    return config


def reset_public_schema(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return validate_test_database_url(raw_url)


@pytest.fixture(scope="session")
def migration_cycle(test_database_url: str) -> MigrationCycleResult:
    """Reset the test schema and prove the full upgrade/downgrade/upgrade cycle."""
    reset_public_schema(test_database_url)
    config = alembic_config(test_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    inspection_engine = create_engine(test_database_url)
    try:
        tables_after_downgrade = frozenset(inspect(inspection_engine).get_table_names())
        with inspection_engine.connect() as connection:
            enum_types = connection.execute(
                text(
                    "SELECT typname FROM pg_type "
                    "WHERE typname IN "
                    "('user_role', 'conversation_type', 'message_role')"
                )
            ).scalars()
            enum_types_after_downgrade = frozenset(enum_types)
    finally:
        inspection_engine.dispose()

    command.upgrade(config, "head")
    return MigrationCycleResult(
        tables_after_downgrade=tables_after_downgrade,
        enum_types_after_downgrade=enum_types_after_downgrade,
    )


@pytest.fixture(scope="session")
def integration_engine(
    test_database_url: str,
    migration_cycle: MigrationCycleResult,
) -> Engine:
    del migration_cycle
    engine = create_engine(test_database_url, pool_size=10, max_overflow=10)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_business_tables(integration_engine: Engine):
    """Truncate all business tables around a data-changing integration test."""
    with integration_engine.begin() as connection:
        connection.execute(
            text("TRUNCATE TABLE email_login_challenges, user_sessions, messages, conversations, users CASCADE")
        )
    yield

    with integration_engine.begin() as connection:
        connection.execute(
            text("TRUNCATE TABLE email_login_challenges, user_sessions, messages, conversations, users CASCADE")
        )


@pytest.fixture
def integration_session_factory(
    integration_engine: Engine,
    clean_business_tables,
):
    del clean_business_tables
    return sessionmaker(
        bind=integration_engine,
        autoflush=False,
        expire_on_commit=False,
    )


@pytest.fixture
def db_session(integration_session_factory) -> Session:
    session = integration_session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()

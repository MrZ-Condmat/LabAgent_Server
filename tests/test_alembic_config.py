from io import StringIO

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from lab_agent.db.base import Base
from lab_agent.db.engine import DatabaseNotConfiguredError, require_database_url
from lab_agent.utils.config import Config


def alembic_config() -> AlembicConfig:
    config = AlembicConfig("alembic.ini")
    config.output_buffer = StringIO()
    return config


def test_base_import_has_no_business_tables():
    assert Base.metadata.tables == {}


def test_database_url_is_read_from_shared_config(monkeypatch):
    database_url = (
        "postgresql+psycopg://migration:p%40ss%25word@localhost:5432/testdb"
    )
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert require_database_url(Config()) == database_url


def test_offline_upgrade_accepts_percent_encoded_database_url(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://migration:p%40ss%25word@localhost:5432/testdb",
    )

    config = alembic_config()
    command.upgrade(config, "head", sql=True)

    assert "p%40ss%25word" not in config.output_buffer.getvalue()


def test_offline_upgrade_requires_database_configuration(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(
        DatabaseNotConfiguredError,
        match="^Database is not configured\\. Set DATABASE_URL\\.$",
    ) as error:
        command.upgrade(alembic_config(), "head", sql=True)

    assert "password" not in str(error.value).lower()
    assert "postgresql" not in str(error.value).lower()

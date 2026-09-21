import pytest

from lab_agent.db import engine as engine_module
from lab_agent.utils.config import Config


DATABASE_ENV_KEYS = (
    "DATABASE_URL",
    "DATABASE_POOL_SIZE",
    "DATABASE_MAX_OVERFLOW",
    "DATABASE_POOL_TIMEOUT",
    "DATABASE_ECHO",
)


@pytest.fixture(autouse=True)
def reset_database_engine():
    engine_module.dispose_database_engine()
    yield
    engine_module.dispose_database_engine()


def test_postgresql_config_is_read_from_environment(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://test:test@localhost:5432/testdb",
    )
    monkeypatch.setenv("DATABASE_POOL_SIZE", "7")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "9")
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT", "45")
    monkeypatch.setenv("DATABASE_ECHO", "true")

    config = Config()

    assert config.database_url == "postgresql+psycopg://test:test@localhost:5432/testdb"
    assert config.database_pool_size == 7
    assert config.database_max_overflow == 9
    assert config.database_pool_timeout == 45
    assert config.database_echo is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("false", False),
        ("0", False),
        ("no", False),
        ("true", True),
        ("1", True),
        ("yes", True),
        ("ON", True),
    ],
)
def test_database_echo_boolean_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("DATABASE_ECHO", value)

    assert Config().database_echo is expected


def test_database_is_optional_until_engine_is_requested(monkeypatch):
    for key in DATABASE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    config = Config()

    assert config.database_url is None
    with pytest.raises(
        engine_module.DatabaseNotConfiguredError,
        match="Database is not configured. Set DATABASE_URL.",
    ):
        engine_module.get_database_engine(config)

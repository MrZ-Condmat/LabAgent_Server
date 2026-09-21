import importlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sqlalchemy

from lab_agent.db import engine as engine_module
from lab_agent.db import session as session_module


@pytest.fixture(autouse=True)
def reset_database_state():
    engine_module.dispose_database_engine()
    session_module.reset_session_factory()
    yield
    engine_module.dispose_database_engine()
    session_module.reset_session_factory()


def database_config():
    return SimpleNamespace(
        database_url="postgresql+psycopg://test:test@localhost:5432/testdb",
        database_pool_size=5,
        database_max_overflow=6,
        database_pool_timeout=20,
        database_echo=False,
    )


def test_import_does_not_create_an_engine(monkeypatch):
    create_engine = Mock()
    monkeypatch.setattr(sqlalchemy, "create_engine", create_engine)

    importlib.reload(engine_module)

    create_engine.assert_not_called()
    assert engine_module._engine is None


def test_engine_receives_expected_pool_options(monkeypatch):
    expected_engine = Mock()
    create_engine = Mock(return_value=expected_engine)
    monkeypatch.setattr(engine_module, "create_engine", create_engine)

    result = engine_module.create_database_engine(database_config())

    assert result is expected_engine
    create_engine.assert_called_once_with(
        "postgresql+psycopg://test:test@localhost:5432/testdb",
        pool_size=5,
        max_overflow=6,
        pool_timeout=20,
        pool_pre_ping=True,
        echo=False,
    )


def test_engine_is_reused_and_can_be_recreated(monkeypatch):
    first_engine = Mock()
    second_engine = Mock()
    create_engine = Mock(side_effect=[first_engine, second_engine])
    monkeypatch.setattr(engine_module, "create_engine", create_engine)

    assert engine_module.get_database_engine(database_config()) is first_engine
    assert engine_module.get_database_engine(database_config()) is first_engine
    assert create_engine.call_count == 1

    engine_module.dispose_database_engine()

    first_engine.dispose.assert_called_once_with()
    assert engine_module.get_database_engine(database_config()) is second_engine
    assert create_engine.call_count == 2


def test_session_factory_is_bound_without_connecting(monkeypatch):
    engine = Mock()
    expected_factory = Mock()
    make_session_factory = Mock(return_value=expected_factory)
    monkeypatch.setattr(session_module, "sessionmaker", make_session_factory)

    result = session_module.get_session_factory(engine)

    assert result is expected_factory
    make_session_factory.assert_called_once_with(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


def test_database_session_commits_and_closes():
    session = Mock()
    factory = Mock(return_value=session)

    with session_module.database_session(factory) as active_session:
        assert active_session is session

    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()


def test_database_session_rolls_back_and_closes_on_error():
    session = Mock()
    factory = Mock(return_value=session)

    with pytest.raises(ValueError, match="failed unit of work"):
        with session_module.database_session(factory):
            raise ValueError("failed unit of work")

    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()

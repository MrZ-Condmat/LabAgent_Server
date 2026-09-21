"""SQLAlchemy Engine creation and lazy process-level reuse."""

from threading import Lock
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from lab_agent.utils import Config


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when database infrastructure is used without configuration."""


_engine: Optional[Engine] = None
_engine_lock = Lock()


def create_database_engine(config: Optional[Config] = None) -> Engine:
    """Create an Engine without opening a database connection."""
    database_config = config or Config()
    if not database_config.database_url:
        raise DatabaseNotConfiguredError(
            "Database is not configured. Set DATABASE_URL."
        )

    return create_engine(
        database_config.database_url,
        pool_size=database_config.database_pool_size,
        max_overflow=database_config.database_max_overflow,
        pool_timeout=database_config.database_pool_timeout,
        pool_pre_ping=True,
        echo=database_config.database_echo,
    )


def get_database_engine(config: Optional[Config] = None) -> Engine:
    """Return the lazily created process-level Engine."""
    global _engine

    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = create_database_engine(config)
    return _engine


def dispose_database_engine() -> None:
    """Dispose the cached Engine and allow a later call to recreate it."""
    global _engine

    with _engine_lock:
        engine = _engine
        _engine = None

    if engine is not None:
        engine.dispose()

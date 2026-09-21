"""Session factory and transaction-scoped context manager."""

from contextlib import contextmanager
from threading import Lock
from typing import Callable, Iterator, Optional

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .engine import get_database_engine


SessionFactory = Callable[[], Session]

_session_factory: Optional[SessionFactory] = None
_session_factory_engine: Optional[Engine] = None
_session_factory_lock = Lock()


def get_session_factory(engine: Optional[Engine] = None) -> SessionFactory:
    """Return a session factory bound to the supplied or default Engine."""
    global _session_factory, _session_factory_engine

    target_engine = engine if engine is not None else get_database_engine()
    if _session_factory is None or _session_factory_engine is not target_engine:
        with _session_factory_lock:
            if _session_factory is None or _session_factory_engine is not target_engine:
                _session_factory = sessionmaker(
                    bind=target_engine,
                    autoflush=False,
                    expire_on_commit=False,
                )
                _session_factory_engine = target_engine
    return _session_factory


def reset_session_factory() -> None:
    """Clear the cached factory without connecting to or disposing an Engine."""
    global _session_factory, _session_factory_engine

    with _session_factory_lock:
        _session_factory = None
        _session_factory_engine = None


@contextmanager
def database_session(
    session_factory: Optional[SessionFactory] = None,
) -> Iterator[Session]:
    """Commit a successful unit of work and rollback failed work."""
    factory = session_factory if session_factory is not None else get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

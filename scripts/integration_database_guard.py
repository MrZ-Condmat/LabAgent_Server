"""Fail-closed identity gates for the single disposable Compose test instance."""

import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url


TEST_DATABASE = "labagent_integration_test"
TEST_USER = "labagent_test"
CLUSTER_MARKER = "labagent-disposable-integration"


def validate_test_database_url(raw_url):
    if os.environ.get("LABAGENT_ALLOW_DESTRUCTIVE_INTEGRATION_TESTS") != "1":
        raise RuntimeError("Destructive integration tests require explicit opt-in=1.")
    try:
        url = make_url(raw_url)
        matches = (url.drivername == "postgresql+psycopg" and url.host == "127.0.0.1"
                   and url.port == 55432 and url.database == TEST_DATABASE
                   and url.username == TEST_USER and url.password == "test_password"
                   and not url.query)
    except Exception:
        matches = False
    if not matches:
        raise RuntimeError("TEST_DATABASE_URL must match the dedicated local disposable Compose instance.")
    return raw_url


def verify_server_identity(dbapi_connection):
    """Called on EVERY pool checkout, before any test SQL can execute."""
    try:
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user, current_setting('cluster_name'), "
                           "inet_server_port(), current_setting('server_version_num')::int")
            database, user, marker, port, version = cursor.fetchone()
        dbapi_connection.rollback()
        if not (database == TEST_DATABASE and user == TEST_USER and marker == CLUSTER_MARKER
                and port == 5432 and 160000 <= version < 170000):
            raise RuntimeError("Disposable PostgreSQL server identity mismatch; destructive SQL blocked.")
    except Exception:
        raise RuntimeError("Unable to confirm disposable PostgreSQL identity; destructive SQL blocked.") from None


def guarded_test_engine(raw_url, **kwargs):
    validate_test_database_url(raw_url)
    engine = create_engine(raw_url, **kwargs)

    @event.listens_for(engine, "checkout")
    def validate_checkout(dbapi_connection, _record, _proxy):
        validate_test_database_url(raw_url)  # Opt-in remains mandatory for reused connections.
        verify_server_identity(dbapi_connection)

    return engine

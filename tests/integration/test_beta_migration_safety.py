"""C3 executes fixture revisions only inside a guarded disposable PostgreSQL DB."""

import uuid

from alembic import command
import pytest
from sqlalchemy import create_engine, text

from lab_agent.db import migration_safety as s


pytestmark = pytest.mark.integration


def test_fixture_upgrade_failure_and_same_connection_lock(test_database_url, tmp_path):
    # test_database_url rejects non-test names and never falls back to DATABASE_URL.
    root = tmp_path / "fixture"
    versions = root / "alembic/versions"
    versions.mkdir(parents=True)
    (root / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (root / "alembic/env.py").write_text(
        "from alembic import context\nc=context.config.attributes['connection']\n"
        "context.configure(connection=c)\nwith context.begin_transaction(): context.run_migrations()\n", encoding="utf-8")
    for revision, parent, body in (("fixture_old", None, "pass"),
                                   ("fixture_new", "fixture_old", "pass"),
                                   ("fixture_bad", "fixture_new", "raise RuntimeError('fixture failure')")):
        (versions / f"{revision}.py").write_text(
            f"revision={revision!r}\ndown_revision={parent!r}\ndef upgrade(): {body}\n"
            "def downgrade(): pass\n", encoding="utf-8")
    schema = "c3_test_" + uuid.uuid4().hex
    engine = create_engine(test_database_url)
    config = s.migration_config(root)
    plan = {"release_id": "fixture", "git_revision": "a" * 40,
            "identity": {"database_name": "fixture_test", "revision": "fixture_old"},
            "target_revision": "fixture_new"}
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            assert connection.execute(text("SELECT pg_try_advisory_lock(741613330931)")).scalar_one()
            connection.commit()
            with engine.connect() as other:
                assert not other.execute(text("SELECT pg_try_advisory_lock(741613330931)")).scalar_one()
            config.attributes["connection"] = connection
            command.upgrade(config, "fixture_old")
            s.apply_plan(connection, config, plan, backup={}, audit_dir=tmp_path / "audit")
            connection.rollback()
            plan["target_revision"] = "fixture_bad"
            with pytest.raises(s.MigrationBlocked):
                s.apply_plan(connection, config, plan, backup={}, audit_dir=tmp_path / "audit")
            connection.rollback()
            assert s.MigrationContext.configure(connection).get_current_heads() == ("fixture_new",)
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(741613330931)"))
            connection.commit()
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()

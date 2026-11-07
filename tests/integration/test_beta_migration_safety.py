"""C3 executes fixture revisions only inside a guarded disposable PostgreSQL DB."""

import uuid
import json
from pathlib import Path

from alembic import command
import pytest
from sqlalchemy import text
from scripts.integration_database_guard import guarded_test_engine as create_engine

from lab_agent.db import migration_safety as s


pytestmark = pytest.mark.integration


def test_fixture_upgrade_failure_and_same_connection_lock(test_database_url, tmp_path, monkeypatch):
    # URL + opt-in + server marker/user/version gates precede every connection checkout.
    root = tmp_path / "fixture"
    versions = root / "alembic/versions"
    versions.mkdir(parents=True)
    project = Path(__file__).resolve().parents[2]
    (root / "alembic.ini").write_bytes((project / "alembic.ini").read_bytes())
    (root / "alembic/env.py").write_bytes((project / "alembic/env.py").read_bytes())
    for revision, parent, body in (("fixture_old", None, "pass"),
                                   ("fixture_new", "fixture_old", "op.execute('CREATE TABLE c3_probe (id int)')"),
                                   ("fixture_bad", "fixture_new", "op.execute('CREATE TABLE c3_failed (id int)'); raise RuntimeError('fixture failure')")):
        (versions / f"{revision}.py").write_text(
            f"from alembic import op\nrevision={revision!r}\ndown_revision={parent!r}\ndef upgrade(): {body}\n"
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
            connection.commit()
            config.attributes["connection"] = connection
            command.upgrade(config, "fixture_old")
            with s.migration_lock(connection):
                with engine.connect() as other:
                    with pytest.raises(s.MigrationBlocked, match="Another migration"):
                        with s.migration_lock(other):
                            pytest.fail("Contending migration acquired lock")
                s.apply_plan(connection, config, plan, backup={}, audit_dir=tmp_path / "audit")
                # A separate connection must see durable revision AND DDL, before lock release.
                with engine.connect() as other:
                    other.execute(text(f'SET search_path TO "{schema}"'))
                    assert s.MigrationContext.configure(other).get_current_heads() == ("fixture_new",)
                    assert other.execute(text("SELECT to_regclass('c3_probe') IS NOT NULL")).scalar_one()
                plan["target_revision"] = "fixture_bad"
                with pytest.raises(s.MigrationBlocked):
                    s.apply_plan(connection, config, plan, backup={}, audit_dir=tmp_path / "audit")
                with engine.connect() as other:
                    other.execute(text(f'SET search_path TO "{schema}"'))
                    assert s.MigrationContext.configure(other).get_current_heads() == ("fixture_new",)
                    assert other.execute(text("SELECT to_regclass('c3_failed') IS NULL")).scalar_one()
                # A false final revision rolls back fixture DDL instead of recording success.
                plan["target_revision"] = "fixture_old"
                with monkeypatch.context() as changed:
                    changed.setattr(s.command, "upgrade", lambda *_: connection.execute(text("CREATE TABLE c3_mismatch (id int)")))
                    with pytest.raises(s.MigrationBlocked):
                        s.apply_plan(connection, config, plan, backup={}, audit_dir=tmp_path / "audit")
                with engine.connect() as other:
                    other.execute(text(f'SET search_path TO "{schema}"'))
                    assert other.execute(text("SELECT to_regclass('c3_mismatch') IS NULL")).scalar_one()
            with engine.connect() as other:
                with s.migration_lock(other):
                    pass  # Lock was released even after failed migration.
            records = [json.loads(p.read_text()) for p in (tmp_path / "audit").glob("*.json")]
            assert sum(r["outcome"] == "SUCCEEDED" for r in records) == 1
            assert sum(r["outcome"] == "FAILED_REQUIRES_INSPECTION" for r in records) == 2
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()

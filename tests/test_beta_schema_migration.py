"""Migration gates use fake identities and isolated, temporary revision graphs."""

from datetime import timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest
from sqlalchemy import create_engine

from lab_agent.db import migration_safety as s
from scripts import migrate_beta_schema as cli


@pytest.fixture
def graph(tmp_path):
    root = tmp_path / "release"
    versions = root / "alembic/versions"
    versions.mkdir(parents=True)
    (root / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    for name, parent in (("z_old", None), ("a_new", "z_old")):
        (versions / f"{name}.py").write_text(
            f"revision = {name!r}\ndown_revision = {parent!r}\n"
            "def upgrade(): pass\ndef downgrade(): pass\n", encoding="utf-8")
    return root


def test_plan_graph_uses_ancestry_not_lexical_order(graph):
    assert s.upgrade_path(graph, "z_old", "head") == ("a_new", ["a_new"])
    assert s.upgrade_path(graph, "a_new", "head") == ("a_new", [])


@pytest.mark.parametrize("current,target", [(None, "head"), ("unknown", "head"), ("a_new", "z_old")])
def test_unknown_missing_or_newer_revision_refused(graph, current, target):
    with pytest.raises(s.MigrationBlocked):
        s.upgrade_path(graph, current, target)


@pytest.mark.parametrize("parent", [None, "missing_parent"])
def test_multiple_heads_or_broken_graph_refused(graph, parent):
    (graph / "alembic/versions/branch.py").write_text(
        f"revision='branch'\ndown_revision={parent!r}\n", encoding="utf-8")
    with pytest.raises(s.MigrationBlocked):
        s.upgrade_path(graph, "z_old", "head")


def test_wrong_database_refused():
    connection = Mock()
    connection.execute.return_value.one.return_value = ("wrong", "127.0.0.1", 5432)
    with pytest.raises(s.MigrationBlocked, match="identity"):
        s.database_state(connection, "expected")


def test_missing_alembic_version_refused(monkeypatch):
    connection = Mock()
    connection.execute.return_value.one.return_value = ("expected", "127.0.0.1", 5432)
    monkeypatch.setattr(s.MigrationContext, "configure", lambda c: SimpleNamespace(get_current_heads=lambda: ()))
    with pytest.raises(s.MigrationBlocked, match="Missing"):
        s.database_state(connection, "expected")


@pytest.fixture
def backup(tmp_path, monkeypatch):
    path = tmp_path / "fake.dump"
    path.write_bytes(b"PGDMPfake-test-only")
    now = s.utc_now()
    data = {"database_name": "expected", "alembic_revision": "old", "created_at_utc": now.isoformat(),
            "postgres_version": "PostgreSQL test", "backup_filename": path.name,
            "backup_size_bytes": path.stat().st_size, "sha256": s.digest(path)}
    Path(str(path) + ".json").write_text(json.dumps(data), encoding="utf-8")
    Path(str(path) + ".sha256").write_text(f"{data['sha256']}  {path.name}\n", encoding="utf-8")
    monkeypatch.setattr(s.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(s.shutil, "which", lambda name: "/fake/pg_restore")
    return path, data, now


def check_backup(backup):
    path, _, now = backup
    return s.verify_backup(path, path.parent, {"database_name": "expected", "revision": "old"},
                           not_before=now - timedelta(minutes=1), now=now)


def test_backup_valid(backup):
    assert check_backup(backup)["backup_sha256"] == backup[1]["sha256"]


def test_backup_uses_only_local_pg_restore(backup, monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(s.subprocess, "run", run)
    check_backup(backup)
    assert calls == [["/fake/pg_restore", "--list", str(backup[0])]]


def test_missing_local_client_has_no_elevation_fallback(backup, monkeypatch):
    monkeypatch.setattr(s.shutil, "which", lambda name: None)
    run = Mock()
    monkeypatch.setattr(s.subprocess, "run", run)
    with pytest.raises(s.MigrationBlocked, match="Local pg_restore is unavailable"):
        check_backup(backup)
    run.assert_not_called()


def test_unreadable_archive_is_distinct(backup, monkeypatch):
    monkeypatch.setattr(s.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(s.MigrationBlocked, match="archive readability"):
        check_backup(backup)


@pytest.mark.parametrize("field,value", [("database_name", "wrong"), ("alembic_revision", "wrong"),
    ("sha256", "0" * 64), ("backup_size_bytes", 0), ("created_at_utc", "2000-01-01T00:00:00Z")])
def test_invalid_backup_metadata_refused(backup, field, value):
    path, data, _ = backup
    data[field] = value
    Path(str(path) + ".json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(s.MigrationBlocked):
        check_backup(backup)


def test_checksum_sidecar_and_allowed_directory_required(backup, tmp_path):
    path, _, now = backup
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(s.MigrationBlocked):
        s.verify_backup(path, tmp_path / "elsewhere", {}, not_before=now)
    Path(str(path) + ".sha256").write_text("bad", encoding="utf-8")
    with pytest.raises(s.MigrationBlocked):
        check_backup(backup)


def test_missing_backup_refused(tmp_path):
    with pytest.raises(s.MigrationBlocked, match="missing"):
        s.verify_backup(tmp_path / "absent", tmp_path, {}, not_before=s.utc_now())


def test_backup_must_follow_plan(backup):
    path, _, now = backup
    with pytest.raises(s.MigrationBlocked, match="after"):
        s.verify_backup(path, path.parent, {"database_name": "expected", "revision": "old"},
                        not_before=now + timedelta(seconds=1), now=now)


def test_maintenance_rejects_active_or_unknown_service(monkeypatch):
    monkeypatch.setattr(s.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="active"))
    with pytest.raises(s.MigrationBlocked):
        s.check_maintenance()


def test_migration_failure_audited_without_secret_or_recovery(tmp_path, monkeypatch):
    plan = {"release_id": "fake", "git_revision": "a" * 40,
            "identity": {"database_name": "test", "revision": "old"}, "target_revision": "new"}
    calls = []
    def fail(*args):
        calls.append(args)
        raise RuntimeError("password=NEVER_PRINT")
    monkeypatch.setattr(s.command, "upgrade", fail)
    connection = MagicMock()
    connection.in_transaction.return_value = False
    with pytest.raises(s.MigrationBlocked) as error:
        s.apply_plan(connection, SimpleNamespace(attributes={}), plan,
                     backup={"backup_filename": "test.dump", "backup_sha256": "0" * 64}, audit_dir=tmp_path)
    assert len(calls) == 1
    assert "NEVER_PRINT" not in str(error.value)
    records = list(tmp_path.glob("*.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text())["outcome"] == "FAILED_REQUIRES_INSPECTION"
    assert "NEVER_PRINT" not in records[0].read_text()


def test_wrong_final_revision_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(s.command, "upgrade", lambda *a: None)
    monkeypatch.setattr(s.MigrationContext, "configure", lambda c: SimpleNamespace(get_current_heads=lambda: ("wrong",)))
    plan = {"release_id": "fake", "git_revision": "a" * 40,
            "identity": {"database_name": "test", "revision": "old"}, "target_revision": "new"}
    connection = MagicMock()
    connection.in_transaction.return_value = False
    with pytest.raises(s.MigrationBlocked):
        s.apply_plan(connection, SimpleNamespace(attributes={}), plan, backup={}, audit_dir=tmp_path)
    connection.begin.assert_called_once()


@pytest.mark.parametrize("failure", ["missing_url", "connection_failed", "wrong_endpoint"])
def test_cli_safe_failures(tmp_path, monkeypatch, capsys, failure):
    runtime = tmp_path / "runtime.env"
    runtime.write_text("", encoding="utf-8")
    monkeypatch.setenv("LABAGENT_ENV_FILE", str(runtime))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if failure != "missing_url":
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:NEVER_PRINT@localhost:55432/test")
    def fail(*a, **k):
        raise RuntimeError("NEVER_PRINT")
    monkeypatch.setattr(cli, "create_engine", fail)
    args = ["plan", "--expected-db", "test", "--expected-host", "localhost",
            "--expected-port", "55432" if failure != "wrong_endpoint" else "55433",
            "--target-revision", "head", "--plan", str(tmp_path / "plan.json")]
    assert cli.main(args) == 1
    assert "NEVER_PRINT" not in capsys.readouterr().err


def test_real_fixture_upgrade_and_failed_upgrade_offline(graph, tmp_path):
    # Official Alembic API executes real fixture migrations against isolated SQLite.
    (graph / "alembic/env.py").write_text(
        "from alembic import context\nc=context.config.attributes['connection']\n"
        "context.configure(connection=c)\nwith context.begin_transaction(): context.run_migrations()\n", encoding="utf-8")
    engine = create_engine("sqlite://")
    from alembic import command
    config = s.migration_config(graph)
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "z_old")
        connection.commit()
        plan = {"release_id": "test", "git_revision": "a" * 40,
                "identity": {"database_name": "fixture_test", "revision": "z_old"}, "target_revision": "a_new"}
        s.apply_plan(connection, config, plan, backup={}, audit_dir=tmp_path / "audit")
        connection.commit()
        assert s.MigrationContext.configure(connection).get_current_heads() == ("a_new",)
        connection.rollback()
        (graph / "alembic/versions/bad.py").write_text(
            "revision='fixture_bad'\ndown_revision='a_new'\n"
            "def upgrade(): raise RuntimeError('fixture failure')\n"
            "def downgrade(): pass\n", encoding="utf-8")
        plan["target_revision"] = "fixture_bad"
        with pytest.raises(s.MigrationBlocked):
            s.apply_plan(connection, config, plan, backup={}, audit_dir=tmp_path / "audit")
        connection.rollback()
        assert s.MigrationContext.configure(connection).get_current_heads() == ("a_new",)
    engine.dispose()


@pytest.mark.parametrize("gate", ["approval", "backup", "changed", "repeat", "lock", "maintenance", "success"])
def test_apply_orchestration_requires_all_gates(tmp_path, monkeypatch, gate):
    runtime = tmp_path / "runtime.env"
    runtime.write_text("", encoding="utf-8")
    monkeypatch.setenv("LABAGENT_ENV_FILE", str(runtime))
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:fake@localhost:55432/test")
    connection = Mock()
    connection.execute.return_value.scalar_one.return_value = gate != "lock"
    context = Mock()
    context.__enter__ = Mock(return_value=connection)
    context.__exit__ = Mock(return_value=False)
    engine = Mock()
    engine.connect.return_value = context
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: engine)
    state = {"identity": {"database_name": "test", "revision": "old"}, "target_revision": "new",
             "migration_path": ["new"], "release_id": "fixture", "git_revision": "a" * 40}
    if gate == "repeat":
        state["migration_path"] = []
    monkeypatch.setattr(s, "snapshot", lambda *a: dict(state))
    saved = {**state, "connection_host": "localhost", "connection_port": 55432}
    plan_file = tmp_path / "plan.json"
    s.atomic_json(plan_file, {"created_at_utc": s.utc_now().isoformat(),
                            "snapshot": {} if gate == "changed" else saved})
    monkeypatch.setattr(s, "verify_backup", lambda *a, **k: {})
    def maintenance():
        if gate == "maintenance":
            raise s.MigrationBlocked("Writers still running")
    monkeypatch.setattr(s, "check_maintenance", maintenance)
    execute = Mock()
    monkeypatch.setattr(s, "apply_plan", execute)
    args = ["apply", "--expected-db", "test", "--expected-host", "localhost", "--expected-port", "55432",
            "--target-revision", "new", "--plan", str(plan_file)]
    if gate != "approval":
        args += ["--authorize-upgrade", "--confirm-maintenance", "--approve-code-schema-compatibility"]
    if gate != "backup":
        args += ["--backup", str(tmp_path / "fake.dump")]
    assert cli.main(args) == (0 if gate == "success" else 1)
    assert execute.call_count == (1 if gate == "success" else 0)


def test_audit_failure_blocks_ddl(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "atomic_json", Mock(side_effect=OSError("disk full")))
    upgrade = Mock()
    monkeypatch.setattr(s.command, "upgrade", upgrade)
    with pytest.raises(OSError):
        s.apply_plan(Mock(), SimpleNamespace(attributes={}),
                     {"release_id": "x", "git_revision": "a" * 40,
                      "identity": {"database_name": "test", "revision": "old"}, "target_revision": "new"},
                     backup={}, audit_dir=tmp_path)
    upgrade.assert_not_called()

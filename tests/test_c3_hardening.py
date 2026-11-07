"""Fail-closed gates: no network, Docker socket, or privileged operation."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from pathlib import Path
import importlib.util
import sys

import pytest

from scripts import integration_database_guard as guard
from lab_agent.db import migration_safety as s


URL = "postgresql+psycopg://labagent_test:test_password@127.0.0.1:55432/labagent_integration_test"


def test_exact_local_identity_is_accepted(monkeypatch):
    monkeypatch.setenv("LABAGENT_ALLOW_DESTRUCTIVE_INTEGRATION_TESTS", "1")
    assert guard.validate_test_database_url(URL) == URL
    dbapi = MagicMock()
    cursor = dbapi.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (guard.TEST_DATABASE, guard.TEST_USER, guard.CLUSTER_MARKER, 5432, 160006)
    guard.verify_server_identity(dbapi)
    dbapi.rollback.assert_called_once()


def test_missing_test_url_never_falls_back_to_production(monkeypatch):
    path = Path(__file__).parent / "integration/conftest.py"
    spec = importlib.util.spec_from_file_location("c3_isolation_fixture_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://fake:fake@127.0.0.1:55433/labagent_beta")
    with pytest.raises(pytest.skip.Exception, match="TEST_DATABASE_URL"):
        module.test_database_url.__wrapped__()


@pytest.mark.parametrize("url", [URL.replace("55432", "55433"), URL.replace("127.0.0.1", "localhost"),
    URL.replace("127.0.0.1", "166.111.26.183"), URL.replace("127.0.0.1", "db.example.org"),
    URL.replace("labagent_integration_test", "labagent_beta"), URL.replace("labagent_test:", "labagent:"),
    URL.replace("test_password", "other"), URL + "?host=remote.example.org",
    URL.replace("labagent_integration_test", "anything_test"), "bad-url"])
def test_wrong_test_endpoint_never_connects(monkeypatch, url):
    monkeypatch.setenv("LABAGENT_ALLOW_DESTRUCTIVE_INTEGRATION_TESTS", "1")
    connect = Mock()
    monkeypatch.setattr(guard, "create_engine", connect)
    with pytest.raises(RuntimeError):
        guard.guarded_test_engine(url)
    connect.assert_not_called()


@pytest.mark.parametrize("optin", [None, "0", "true", "yes"])
def test_correct_endpoint_still_needs_explicit_optin(monkeypatch, optin):
    monkeypatch.delenv("LABAGENT_ALLOW_DESTRUCTIVE_INTEGRATION_TESTS", raising=False)
    if optin is not None:
        monkeypatch.setenv("LABAGENT_ALLOW_DESTRUCTIVE_INTEGRATION_TESTS", optin)
    with pytest.raises(RuntimeError):
        guard.validate_test_database_url(URL)


@pytest.mark.parametrize("row", [
    ("labagent_beta", "labagent_test", guard.CLUSTER_MARKER, 5432, 160000),
    (guard.TEST_DATABASE, "labagent", guard.CLUSTER_MARKER, 5432, 160000),
    (guard.TEST_DATABASE, guard.TEST_USER, "", 5432, 160000),
    (guard.TEST_DATABASE, guard.TEST_USER, guard.CLUSTER_MARKER, 55433, 160000),
    (guard.TEST_DATABASE, guard.TEST_USER, guard.CLUSTER_MARKER, 5432, 150000),
])
def test_actual_server_mismatch_blocks_sql(row):
    dbapi = MagicMock()
    cursor = dbapi.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = row
    with pytest.raises(RuntimeError, match="identity"):
        guard.verify_server_identity(dbapi)
    assert cursor.execute.call_count == 1
    assert "SELECT current_database()" in cursor.execute.call_args.args[0]


@pytest.mark.parametrize("writer", [
    "bash /data/zmr/projects/labAgent_Server/scripts/run_daily_report.sh",
    "python scripts/generate_daily_report.py --skip-weekends",
    "python /data/zmr/projects/labAgent_Server/scripts/rescore_saved_reports.py",
    "python -m lab_agent.admin bootstrap",
    "python -m lab_agent.mcp.mcp_server",
    "python /project/lab_agent/mcp/mcp_server.py",
    "python -m uvicorn lab_agent.api.app:app --port 8000",
    "python -m streamlit run lab_agent/web/app.py --server.port 8501",
])
def test_known_writer_blocks_apply(monkeypatch, writer):
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="inactive" if argv[0] == "systemctl" else f"999999 {writer}\n")
    monkeypatch.setattr(s.subprocess, "run", run)
    with pytest.raises(s.MigrationBlocked, match="writer"):
        s.check_maintenance()


def test_unrelated_process_and_self_are_ignored(monkeypatch):
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="inactive" if argv[0] == "systemctl" else
                               f"{s.os.getpid()} python scripts/migrate_beta_schema.py apply\n"
                               "987654 python unrelated_research.py\n")
    monkeypatch.setattr(s.subprocess, "run", run)
    s.check_maintenance()


@pytest.mark.parametrize("mode", ["unknown", "process_error", "empty", "timeout"])
def test_uncertain_maintenance_blocks(monkeypatch, mode):
    def run(argv, **kwargs):
        if mode == "timeout":
            raise s.subprocess.TimeoutExpired(argv, 10)
        if argv[0] == "systemctl":
            return SimpleNamespace(returncode=0, stdout="unknown" if mode == "unknown" else "inactive")
        return SimpleNamespace(returncode=1 if mode == "process_error" else 0, stdout="")
    monkeypatch.setattr(s.subprocess, "run", run)
    with pytest.raises(s.MigrationBlocked):
        s.check_maintenance()


def test_backup_permission_failure_is_distinct(monkeypatch, tmp_path):
    monkeypatch.setattr(s, "_verify_backup", Mock(side_effect=PermissionError("PRIVATE")))
    with pytest.raises(s.MigrationBlocked, match="access denied") as error:
        s.verify_backup(tmp_path, tmp_path, {}, not_before=s.utc_now())
    assert "PRIVATE" not in str(error.value)


def test_lock_release_error_discards_connection():
    connection = Mock()
    connection.execute.return_value.scalar_one.side_effect = [True, False]
    with pytest.raises(s.MigrationBlocked, match="discarded"):
        with s.migration_lock(connection):
            pass
    connection.invalidate.assert_called_once()


def test_commit_failure_never_audits_success(monkeypatch, tmp_path):
    connection = MagicMock()
    connection.in_transaction.return_value = False
    connection.begin.return_value.__exit__.side_effect = OSError("commit failed private")
    monkeypatch.setattr(s.command, "upgrade", Mock())
    monkeypatch.setattr(s.MigrationContext, "configure", lambda c: SimpleNamespace(get_current_heads=lambda: ("new",)))
    plan = {"release_id": "fixture", "git_revision": "a" * 40, "target_revision": "new",
            "identity": {"database_name": "test", "revision": "old"}}
    with pytest.raises(s.MigrationBlocked):
        s.apply_plan(connection, SimpleNamespace(attributes={}), plan, backup={}, audit_dir=tmp_path)
    import json
    assert json.loads(next(tmp_path.glob("*.json")).read_text())["outcome"] == "FAILED_REQUIRES_INSPECTION"

"""Offline tests for Beta PostgreSQL logical backup and restore scripts."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = ROOT / "scripts" / "backup_beta_postgres.sh"
RESTORE_SCRIPT = ROOT / "scripts" / "restore_beta_postgres.sh"


def _bash_executable() -> str | None:
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        return str(candidate) if candidate.is_file() else None
    return shutil.which("bash")


def _bash_path(bash: str, path: Path) -> str:
    if os.name != "nt":
        return str(path)
    result = subprocess.run(
        [bash, "-c", "cygpath -u \"$INPUT_PATH\""],
        env={**os.environ, "INPUT_PATH": str(path)},
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _fake_docker_source() -> str:
    return r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"

case "$1" in
  inspect)
    case "$*" in
      *State.Running*) printf 'true\n' ;;
      *State.Health*) printf 'healthy\n' ;;
    esac
    exit 0
    ;;
  exec)
    shift
    if [ "${1:-}" = "-i" ]; then shift; fi
    shift
    command="${1:-}"
    case "$command" in
      pg_isready)
        exit 0
        ;;
      pg_dump)
        if [ "${FAKE_DOCKER_MODE:-success}" = "dump_fail" ]; then exit 9; fi
        printf 'fake-custom-archive'
        exit 0
        ;;
      pg_restore)
        cat >/dev/null
        if [ "${FAKE_DOCKER_MODE:-success}" = "restore_fail" ] && [ "$*" != "pg_restore --list" ]; then
          exit 8
        fi
        exit 0
        ;;
      postgres)
        printf 'postgres (PostgreSQL) 15.example\n'
        exit 0
        ;;
      createdb|dropdb)
        exit 0
        ;;
      psql)
        case "$*" in
          *pg_database*)
            [ "${FAKE_TARGET_EXISTS:-0}" = "1" ] && printf '1\n'
            ;;
          *to_regclass*) printf 't\n' ;;
          *'count(*)'*) printf '2\n' ;;
          *alembic_version*) printf '0004_email_otp_auth_foundation\n' ;;
        esac
        exit 0
        ;;
    esac
    ;;
esac
exit 0
'''


def _run_script(
    tmp_path: Path,
    script: Path,
    *args: str,
    mode: str = "success",
    target_exists: bool = False,
    env_file: Path | None = None,
    backup_dir: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    bash = _bash_executable()
    if bash is None:
        pytest.skip("Bash is unavailable")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(_fake_docker_source(), encoding="utf-8", newline="\n")
    docker_log = tmp_path / "docker.log"

    if env_file is None:
        env_file = tmp_path / "postgres.env"
        env_file.write_text(
            "POSTGRES_DB=labagent\n"
            "POSTGRES_USER=labagent\n"
            "POSTGRES_PASSWORD=example_only_password\n",
            encoding="utf-8",
            newline="\n",
        )

    env = {
        **os.environ,
        "FAKE_DOCKER": _bash_path(bash, fake_docker),
        "FAKE_DOCKER_LOG": _bash_path(bash, docker_log),
        "FAKE_DOCKER_MODE": mode,
        "FAKE_TARGET_EXISTS": "1" if target_exists else "0",
        "SCRIPT": _bash_path(bash, script),
        "LABAGENT_POSTGRES_ENV_FILE": _bash_path(bash, env_file),
    }
    if backup_dir is not None:
        env["LABAGENT_POSTGRES_BACKUP_DIR"] = _bash_path(bash, backup_dir)

    posix_args = [_bash_path(bash, Path(arg)) if Path(arg).is_absolute() else arg for arg in args]
    command = 'chmod +x "$FAKE_DOCKER"; PATH="$(dirname "$FAKE_DOCKER"):/usr/bin:/bin" /usr/bin/bash "$SCRIPT"'
    command += "".join(f' "${{ARG_{index}}}"' for index in range(len(posix_args)))
    for index, value in enumerate(posix_args):
        env[f"ARG_{index}"] = value

    result = subprocess.run(
        [bash, "-c", command],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    log = docker_log.read_text(encoding="utf-8") if docker_log.exists() else ""
    return result, log


def _make_backup_with_checksum(tmp_path: Path) -> Path:
    backup = tmp_path / "labagent_example.dump"
    backup.write_bytes(b"fake-custom-archive")
    checksum = hashlib.sha256(backup.read_bytes()).hexdigest()
    backup.with_name(backup.name + ".sha256").write_text(
        f"{checksum}  {backup.name}\n", encoding="utf-8"
    )
    return backup


def test_backup_creates_validated_archive_checksum_and_manifest(tmp_path):
    backup_dir = tmp_path / "backups"
    result, log = _run_script(tmp_path, BACKUP_SCRIPT, backup_dir=backup_dir)

    assert result.returncode == 0, result.stderr
    dumps = list(backup_dir.glob("labagent_*.dump"))
    assert len(dumps) == 1
    backup = dumps[0]
    assert re.fullmatch(r"labagent_\d{8}_\d{6}\.dump", backup.name)
    manifest = json.loads(backup.with_name(backup.name + ".json").read_text())
    assert manifest["database_name"] == "labagent"
    assert manifest["alembic_revision"] == "0004_email_otp_auth_foundation"
    assert manifest["backup_size_bytes"] == backup.stat().st_size
    assert manifest["sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert "example_only_password" not in json.dumps(manifest)
    assert "pg_dump -U labagent -d labagent -Fc" in log
    assert "pg_restore --list" in log


def test_backup_failure_removes_partial_files(tmp_path):
    backup_dir = tmp_path / "backups"
    result, _ = _run_script(
        tmp_path, BACKUP_SCRIPT, mode="dump_fail", backup_dir=backup_dir
    )

    assert result.returncode != 0
    assert "incomplete output was removed" in result.stderr
    assert not list(backup_dir.iterdir())


def test_backup_rejects_missing_env_file(tmp_path):
    missing = tmp_path / "missing.env"
    result, _ = _run_script(tmp_path, BACKUP_SCRIPT, env_file=missing)
    assert result.returncode != 0
    assert "environment file not found" in result.stderr


def test_backup_rejects_empty_password(tmp_path):
    env_file = tmp_path / "postgres.env"
    env_file.write_text(
        "POSTGRES_DB=labagent\nPOSTGRES_USER=labagent\nPOSTGRES_PASSWORD=\n",
        encoding="utf-8",
    )
    result, _ = _run_script(tmp_path, BACKUP_SCRIPT, env_file=env_file)
    assert result.returncode != 0
    assert "POSTGRES_PASSWORD" in result.stderr


@pytest.mark.parametrize("target", ["labagent", "bad-name", "bad;name"])
def test_restore_rejects_source_or_invalid_target(tmp_path, target):
    backup = _make_backup_with_checksum(tmp_path)
    result, log = _run_script(
        tmp_path, RESTORE_SCRIPT, str(backup), "--target-db", target
    )
    assert result.returncode != 0
    assert "createdb" not in log
    assert "dropdb" not in log


def test_restore_rejects_missing_backup(tmp_path):
    missing = tmp_path / "missing.dump"
    result, log = _run_script(
        tmp_path, RESTORE_SCRIPT, str(missing), "--target-db", "labagent_restore_test"
    )
    assert result.returncode != 0
    assert "missing or empty" in result.stderr
    assert not log


def test_restore_rejects_existing_target_by_default(tmp_path):
    backup = _make_backup_with_checksum(tmp_path)
    result, log = _run_script(
        tmp_path,
        RESTORE_SCRIPT,
        str(backup),
        "--target-db",
        "labagent_restore_test",
        target_exists=True,
    )
    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert "dropdb" not in log


def test_replace_existing_never_allows_source_database(tmp_path):
    backup = _make_backup_with_checksum(tmp_path)
    result, log = _run_script(
        tmp_path,
        RESTORE_SCRIPT,
        str(backup),
        "--target-db",
        "labagent",
        "--replace-existing",
        target_exists=True,
    )
    assert result.returncode != 0
    assert "source Beta database" in result.stderr
    assert "dropdb" not in log


def test_replace_existing_drops_only_named_restore_target(tmp_path):
    backup = _make_backup_with_checksum(tmp_path)
    result, log = _run_script(
        tmp_path,
        RESTORE_SCRIPT,
        str(backup),
        "--target-db",
        "labagent_restore_test",
        "--replace-existing",
        target_exists=True,
    )
    assert result.returncode == 0, result.stderr
    assert "dropdb -U labagent labagent_restore_test" in log
    assert "dropdb -U labagent labagent\n" not in log


def test_restore_uses_safe_options_and_reports_counts(tmp_path):
    backup = _make_backup_with_checksum(tmp_path)
    result, log = _run_script(
        tmp_path,
        RESTORE_SCRIPT,
        str(backup),
        "--target-db",
        "labagent_restore_test",
    )
    assert result.returncode == 0, result.stderr
    assert "createdb -U labagent -T template0 labagent_restore_test" in log
    assert "pg_restore -U labagent -d labagent_restore_test --no-owner --no-privileges --exit-on-error" in log
    assert "users: 2" in result.stdout
    assert "conversations: 2" in result.stdout
    assert "messages: 2" in result.stdout
    assert "dropdb" not in log


def test_restore_failure_cleans_only_new_target(tmp_path):
    backup = _make_backup_with_checksum(tmp_path)
    result, log = _run_script(
        tmp_path,
        RESTORE_SCRIPT,
        str(backup),
        "--target-db",
        "labagent_restore_test",
        mode="restore_fail",
    )
    assert result.returncode != 0
    assert "dropdb -U labagent --if-exists labagent_restore_test" in log
    assert "dropdb -U labagent --if-exists labagent\n" not in log


def test_scripts_keep_credentials_and_storage_safe():
    combined = BACKUP_SCRIPT.read_text(encoding="utf-8") + RESTORE_SCRIPT.read_text(
        encoding="utf-8"
    )
    assert "umask 077" in combined
    assert "chmod 600" in combined
    assert "/data/zmr/labagent_backups/postgres" in combined
    assert "LABAGENT_POSTGRES_BACKUP_DIR" in combined
    assert 'echo "$POSTGRES_PASSWORD"' not in combined
    assert 'cat "$ENV_FILE"' not in combined
    assert "down -v" not in combined
    assert "volume rm" not in combined
    assert "--force-production" not in combined

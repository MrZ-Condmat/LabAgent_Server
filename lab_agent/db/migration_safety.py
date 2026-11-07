"""Explicit Beta migration gates. No connection or write occurs on import."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
import uuid
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text


class MigrationBlocked(RuntimeError):
    """A credential-safe gate failure."""


def utc_now():
    return datetime.now(timezone.utc)


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            os.chmod(temporary, 0o600)
            json.dump(value, output, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def migration_config(root):
    root = Path(root).resolve()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config


def upgrade_path(root, current, target):
    try:
        graph = ScriptDirectory.from_config(migration_config(root))
        heads = graph.get_heads()
        revisions = list(graph.walk_revisions())
        if len(heads) != 1 or any(
            isinstance(r.down_revision, tuple) or r.dependencies or r.is_branch_point
            for r in revisions
        ):
            raise MigrationBlocked("Multiple heads or branched migration graph.")
        resolved = graph.get_revision(target)
        if resolved is None or not current or graph.get_revision(current) is None:
            raise MigrationBlocked("Missing or unknown database/target revision.")
        target = resolved.revision
        path = []
        revision = resolved
        while revision.revision != current:
            path.append(revision.revision)
            if revision.down_revision is None:
                raise MigrationBlocked("Target is not a descendant of the database revision.")
            revision = graph.get_revision(revision.down_revision)
        return target, list(reversed(path))
    except MigrationBlocked:
        raise
    except Exception:
        raise MigrationBlocked("Invalid, disconnected, or ambiguous migration graph.") from None


def code_fingerprint(root):
    root = Path(root)
    paths = [root / "alembic.ini", *sorted((root / "alembic").rglob("*.py"))]
    value = hashlib.sha256()
    for path in paths:
        value.update(path.relative_to(root).as_posix().encode())
        value.update(path.read_bytes())
    return value.hexdigest()


def database_state(connection, expected_db):
    row = connection.execute(text(
        "SELECT current_database(), inet_server_addr()::text, inet_server_port()"
    )).one()
    if row[0] != expected_db:
        raise MigrationBlocked("Database identity does not match --expected-db.")
    heads = MigrationContext.configure(connection).get_current_heads()
    if len(heads) != 1:
        raise MigrationBlocked("Missing Alembic revision or multiple database heads.")
    return {"database_name": row[0], "server_address": row[1],
            "server_port": row[2], "revision": heads[0]}


def release_metadata(root):
    # Strict C1 manifest validation is reused; a Git checkout cannot masquerade as a release.
    from scripts.release_archive import load_manifest
    return load_manifest(Path(root) / "release.json", Path(root).name)


def snapshot(connection, root, expected_db, target, active_root):
    state = database_state(connection, expected_db)
    resolved, path = upgrade_path(root, state["revision"], target)
    release = release_metadata(root)
    active = release_metadata(Path(active_root).resolve())
    return {"identity": state, "target_revision": resolved, "migration_path": path,
            "release_id": release["release_id"], "git_revision": release["full_git_sha"],
            "active_release_id": active["release_id"], "active_git_revision": active["full_git_sha"],
            "migration_fingerprint": code_fingerprint(root)}


def verify_backup(path, allowed_dir, state, *, not_before, now=None, max_age=3600):
    try:
        return _verify_backup(path, allowed_dir, state, not_before=not_before, now=now, max_age=max_age)
    except FileNotFoundError:
        raise MigrationBlocked("Backup file, directory or sidecar is missing.") from None
    except PermissionError:
        raise MigrationBlocked("Backup access denied; request restricted read/traverse ACLs from the administrator.") from None
    except (OSError, ValueError, TypeError, KeyError):
        raise MigrationBlocked("Backup metadata or archive could not be read safely.") from None


def _verify_backup(path, allowed_dir, state, *, not_before, now=None, max_age=3600):
    now = now or utc_now()
    path = Path(path).resolve(strict=True)
    allowed = Path(allowed_dir).resolve(strict=True)
    if path.parent != allowed or not path.is_file() or path.stat().st_size == 0:
        raise MigrationBlocked("Backup is outside the allowed directory or empty.")
    manifest_path = Path(str(path) + ".json")
    sha_path = Path(str(path) + ".sha256")
    if manifest_path.is_symlink() or sha_path.is_symlink():
        raise MigrationBlocked("Backup sidecars must be regular files.")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = {"database_name", "created_at_utc", "postgres_version", "alembic_revision",
                "backup_filename", "backup_size_bytes", "sha256"}
    if not isinstance(data, dict) or not required <= data.keys():
        raise MigrationBlocked("Invalid backup manifest schema.")
    actual = digest(path)
    sidecar = sha_path.read_text(encoding="utf-8").split()
    if len(sidecar) != 2 or sidecar != [actual, path.name] or data["sha256"] != actual:
        raise MigrationBlocked("Backup checksum does not match.")
    if (data["database_name"] != state["database_name"] or
            data["alembic_revision"] != state["revision"] or
            data["backup_filename"] != path.name or
            data["backup_size_bytes"] != path.stat().st_size or not data["postgres_version"]):
        raise MigrationBlocked("Backup source, revision, filename or size does not match.")
    created = datetime.fromisoformat(data["created_at_utc"].replace("Z", "+00:00"))
    if created.tzinfo is None or not (0 <= (now - created).total_seconds() <= max_age):
        raise MigrationBlocked("Backup is stale or has a future timestamp.")
    if created < not_before:
        raise MigrationBlocked("Backup must be created after the saved plan.")
    reader = shutil.which("pg_restore")
    if reader is None:
        raise MigrationBlocked("Local pg_restore is unavailable; install a compatible PostgreSQL client. No elevation attempted.")
    try:
        result = subprocess.run([reader, "--list", str(path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=60, check=False)
    except subprocess.TimeoutExpired:
        raise MigrationBlocked("Backup archive readability check timed out.") from None
    if result.returncode:
        raise MigrationBlocked("Backup archive readability check failed.")
    return {"backup_filename": path.name, "backup_sha256": actual}


KNOWN_WRITER_PATTERN = re.compile(
    r"(?:^|[\s/])(?:run_daily_report\.sh|generate_daily_report\.py|rescore_saved_reports\.py|"
    r"bootstrap_admin\.py)(?:\s|$)|"
    r"(?:^|\s)(?:uvicorn\s+lab_agent\.api(?:\.|\s)|"
    r"streamlit\s+run\s+(?:\S*/)?lab_agent/web/app\.py)|"
    r"(?:^|[\s/])mcp_server\.py(?:\s|$)|"
    r"(?:^|\s)-m\s+lab_agent\.(?:admin|mcp\.mcp_server)(?:\s|$)"
)


def check_maintenance():
    try:
        _check_maintenance()
    except (OSError, subprocess.SubprocessError):
        raise MigrationBlocked("Maintenance inspection failed; migration blocked.") from None


def _check_maintenance():
    for service in ("labagent-auth.service", "labagent-web.service"):
        result = subprocess.run(["systemctl", "show", service, "--property=ActiveState", "--value"],
                                capture_output=True, text=True, timeout=10, check=False)
        if result.returncode or result.stdout.strip() != "inactive":
            raise MigrationBlocked("Both LabAgent services must be inactive.")
    # ps does not embed a search expression in its argv, avoiding pgrep -f self matches.
    result = subprocess.run(["ps", "-eo", "pid=,args="],
                            capture_output=True, text=True, timeout=10, check=False)
    if result.returncode:
        raise MigrationBlocked("Process inventory failed; migration blocked.")
    lines = result.stdout.splitlines()
    if not lines:
        raise MigrationBlocked("Empty process inventory; migration blocked.")
    for line in lines:
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            raise MigrationBlocked("Unrecognized process inventory; migration blocked.")
        if int(parts[0]) != os.getpid() and KNOWN_WRITER_PATTERN.search(parts[1]):
            raise MigrationBlocked("Potential writer process exists; migration blocked.")


@contextmanager
def migration_lock(connection, key=741613330931):
    """Session lock retained across commits on exactly the migration connection."""
    try:
        locked = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar_one()
        connection.commit()
    except Exception:
        connection.invalidate()
        raise MigrationBlocked("Unable to acquire migration lock.") from None
    if not locked:
        raise MigrationBlocked("Another migration holds the database lock.")
    try:
        yield
    finally:
        try:
            connection.rollback()
            released = connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key}).scalar_one()
            connection.commit()
            if not released:
                raise RuntimeError("Lock ownership lost")
        except Exception:
            connection.invalidate()
            raise MigrationBlocked("Migration lock cleanup failed; connection discarded; inspect database.") from None


def apply_plan(connection, config, plan, *, backup, audit_dir):
    """Caller holds the advisory lock on this SAME connection throughout execution."""
    record = {"timestamp_utc": utc_now().isoformat(), "release_id": plan["release_id"],
              "git_revision": plan["git_revision"], "database_name": plan["identity"]["database_name"],
              "revision_before": plan["identity"]["revision"], "revision_target": plan["target_revision"],
              "revision_after": None, **backup, "outcome": "STARTED"}
    record.update({"active_release_id": plan.get("active_release_id"),
                   "active_git_revision": plan.get("active_git_revision"),
                   "migration_fingerprint": plan.get("migration_fingerprint"),
                   "operator_approved_code_schema_compatibility": True,
                   "operator_confirmed_maintenance": True})
    audit = Path(audit_dir) / f"{uuid.uuid4().hex}.json"
    atomic_json(audit, record)  # A durable STARTED record is mandatory BEFORE any DDL.
    config.attributes["connection"] = connection
    try:
        if connection.in_transaction():
            raise MigrationBlocked("Migration requires an idle connection with no caller transaction.")
        # Own the transaction: env.py joins it. Context exit MUST commit before success audit.
        with connection.begin():
            command.upgrade(config, plan["target_revision"])
            heads = MigrationContext.configure(connection).get_current_heads()
            if heads != (plan["target_revision"],):
                raise MigrationBlocked("Post-upgrade revision does not match target.")
        heads = MigrationContext.configure(connection).get_current_heads()
        connection.rollback()  # End the post-commit read transaction only.
        if heads != (plan["target_revision"],):
            raise MigrationBlocked("Committed revision does not match target.")
        record["revision_after"] = heads[0]
        record["outcome"] = "SUCCEEDED"
        atomic_json(audit, record)
    except Exception:
        record["outcome"] = "FAILED_REQUIRES_INSPECTION"
        atomic_json(audit, record)
        raise MigrationBlocked("Migration failed; inspect actual schema/revision and retain backup. No automatic recovery.") from None

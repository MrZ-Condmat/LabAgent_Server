"""Read-only plans and explicitly authorized Beta schema upgrades."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from lab_agent.utils.runtime import load_project_dotenv
from lab_agent.db import migration_safety as safety

LOCK_KEY = 741613330931


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=("plan", "verify-backup", "apply"))
    cli.add_argument("--expected-db", required=True)
    cli.add_argument("--expected-host", required=True)
    cli.add_argument("--expected-port", required=True, type=int)
    cli.add_argument("--target-revision", required=True)
    cli.add_argument("--plan", type=Path, required=True)
    cli.add_argument("--backup", type=Path)
    cli.add_argument("--authorize-upgrade", action="store_true")
    cli.add_argument("--confirm-maintenance", action="store_true")
    cli.add_argument("--approve-code-schema-compatibility", action="store_true")
    return cli


def external_path(path):
    resolved = Path(path).resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise safety.MigrationBlocked("Plan, backup and audit paths must remain outside the release.")
    return resolved


def main(argv=None):
    args = parser().parse_args(argv)
    engine = None
    try:
        runtime = os.environ.get("LABAGENT_ENV_FILE", "/data/zmr/projects/labagent_runtime/labagent.env")
        if not Path(runtime).is_file():
            raise safety.MigrationBlocked("External runtime file is missing.")
        load_project_dotenv(runtime)
        raw_url = os.environ.get("DATABASE_URL")
        if not raw_url:
            raise safety.MigrationBlocked("DATABASE_URL is missing.")
        url = make_url(raw_url)
        if (url.drivername != "postgresql+psycopg" or url.database != args.expected_db or
                url.host != args.expected_host or url.port != args.expected_port):
            raise safety.MigrationBlocked("Configured PostgreSQL endpoint does not match explicit expectations.")
        plan_path = external_path(args.plan)
        active_root = Path("/data/zmr/projects/labagent_deploy/current")
        backup_dir = Path(os.environ.get("LABAGENT_POSTGRES_BACKUP_DIR", "/data/zmr/projects/labagent_backups/postgres"))
        audit_dir = external_path(Path("/data/zmr/projects/labagent_shared/logs/migrations"))
        # Never use application echo/debug settings. CLI failures never print driver messages.
        logging.disable(logging.CRITICAL)
        engine = create_engine(url, connect_args={"connect_timeout": 10}, echo=False, poolclass=NullPool)
        with engine.connect() as connection, ExitStack() as locks:
            if args.action == "apply":
                if not (args.authorize_upgrade and args.confirm_maintenance and args.approve_code_schema_compatibility):
                    raise safety.MigrationBlocked("Apply requires upgrade authorization, maintenance confirmation and code/schema approval.")
                connection.execute(text("SET lock_timeout = '10s'"))
                connection.execute(text("SET statement_timeout = '5min'"))
                connection.commit()
                locks.enter_context(safety.migration_lock(connection, LOCK_KEY))
            try:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                current = safety.snapshot(connection, ROOT, args.expected_db, args.target_revision, active_root)
                connection.rollback()
                current["connection_host"] = args.expected_host
                current["connection_port"] = args.expected_port
                if args.action == "plan":
                    saved = {"created_at_utc": safety.utc_now().isoformat(), "snapshot": current}
                    safety.atomic_json(plan_path, saved)
                    print(json.dumps({**current, "status": "READY" if current["migration_path"] else "UP_TO_DATE",
                                      "backup_required": bool(current["migration_path"]),
                                      "maintenance_required": bool(current["migration_path"])}, indent=2))
                    return 0
                saved = json.loads(plan_path.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(saved["created_at_utc"])
                if not timedelta(0) <= safety.utc_now() - created <= timedelta(hours=4):
                    raise safety.MigrationBlocked("Saved plan is expired or dated in the future.")
                if saved["snapshot"] != current:
                    raise safety.MigrationBlocked("Database, release or migration files changed since plan; create a new plan.")
                if not args.backup:
                    raise safety.MigrationBlocked("A verified backup is required.")
                backup = safety.verify_backup(external_path(args.backup), backup_dir,
                                               current["identity"], not_before=created)
                if args.action == "verify-backup":
                    print("Backup source metadata, freshness, checksum and archive verified.")
                    return 0
                if not current["migration_path"]:
                    raise safety.MigrationBlocked("Database is already up to date; no upgrade executed.")
                safety.check_maintenance()
                # Same locked connection is supplied to env.py and retained until upgrade finishes.
                safety.apply_plan(connection, safety.migration_config(ROOT), current,
                                  backup=backup, audit_dir=audit_dir)
                print("Migration succeeded; target revision verified. Activate/restart remain manual.")
                return 0
            finally:
                connection.rollback()
    except safety.MigrationBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1
    except PermissionError:
        print("BLOCKED: file access denied; request narrowly scoped read/traverse ACLs. Do not run apply as root.", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("BLOCKED: a required configuration, plan or backup file is missing.", file=sys.stderr)
        return 1
    except Exception:
        print("BLOCKED: configuration, database, metadata or audit operation failed. No automatic recovery; inspect safely.", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

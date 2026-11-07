# Phase C3: Explicit Beta Schema Migration Safety

The migration CLI is independent of release staging and activation. Run it
using the **candidate staged release's absolute script path** and the shared
Conda Python. Never assume Git HEAD or `current` is the candidate release.
No new production migration is part of C3. The existing head remains
`0004_email_otp_auth_foundation`.

## Audit of the existing architecture

1. The production graph is linear: `0001_create_users` (base),
   `0002_conversations_messages`, `0003_entra_identity`,
   `0004_email_otp_auth_foundation`. Each depends on the preceding revision.
2. `alembic/env.py` uses `config.attributes['database_url']` when provided,
   otherwise shared `Config`/`DATABASE_URL`. C3 adds an optional supplied
   SQLAlchemy connection; existing migration bodies are untouched.
3. `MigrationContext.get_current_heads()` reads the Alembic version table;
   no table or schema is created by preflight. Missing/multiple DB heads fail.
4. A2 backup uses container `pg_dump -Fc`, verifies nonempty output and
   `pg_restore --list`, hashes SHA256, and promotes temporary files.
5. Its manifest contains database_name, created_at_utc, postgres_version,
   alembic_revision, backup_filename, backup_size_bytes, sha256. C3 reuses it
   without changing the A2 format or creating a second dump system.
6. Restore rejects the configured source database even with --replace-existing;
   an existing separate target is rejected by default. A failed restore cleans
   only the target newly created by that invocation.
7. C3 loads the candidate release manifest, uses its own absolute Alembic
   directory, requires one linear revision graph, and fingerprints all migration
   Python files plus alembic.ini. Graph ancestry, never lexical order, decides
   whether the target is reachable. Plan binds candidate/active release IDs and
   Git SHAs, DB identity, revision, endpoint, and graph fingerprint.
8. Auth/Web systemd services, Cron, manually launched report jobs, MCP processes
   or other clients may write concurrently. Stopping two services alone cannot
   prove complete quiescence. External writer inventory is an operator duty.
9. A C1/C2 code rollback cannot reverse a schema/data change. It is allowed only
   after reviewing old-code compatibility with the resulting schema.
10. Unit tests use fake connections and temporary graphs; real API execution
    uses isolated SQLite. PostgreSQL integration uses TEST_DATABASE_URL only,
    its existing test-name guard, and a unique temporary schema. Fixture revisions
    never enter production `alembic/versions/`.

## CLI and privileges

All commands require explicit expected DB, configured host and published port.
Actual `current_database()`, `inet_server_addr()`, `inet_server_port()` and
Alembic heads are read and bound into the plan. PostgreSQL's reported internal
port can differ from Docker's published port; both are retained. This detects
endpoint/identity drift, but does not cryptographically attest a server instance.

Set these operator shell variables (replace the release ID):

```bash
PYTHON=/data/zmr/conda_envs/labagent_server/bin/python
RELEASE=/data/zmr/projects/labagent_deploy/releases/<candidate-release-id>
export LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env
PLAN=/data/zmr/projects/labagent_shared/logs/migrations/candidate-plan.json
# Current head today; use the reviewed candidate revision for a future upgrade.
TARGET=0004_email_otp_auth_foundation
```

The command must come from `$RELEASE`, not from the active compatibility path.
No CLI operation requires sudo. Backup readability uses Docker exec permission;
apply additionally requires PostgreSQL DDL privilege.

### Read-only preflight (no DB writes; services can stay running)

```bash
"$PYTHON" "$RELEASE/scripts/migrate_beta_schema.py" plan \
  --expected-db labagent_beta --expected-host 127.0.0.1 --expected-port 55433 \
  --target-revision "$TARGET" --plan "$PLAN"
```

This writes only a mode-600 external plan file. Database queries run in a
read-only transaction. Exit 0 means READY or UP_TO_DATE; exit 1 means BLOCKED;
invalid CLI usage exits 2. Plans expire after four hours. A change to DB revision,
server endpoint, active/candidate release or migration fingerprint invalidates
the saved plan. Unknown, missing, newer-than-target, broken or branched revisions
are rejected. `head` is accepted only when explicitly requested and uniquely
resolvable; its concrete revision is saved.

### Maintenance and backup (explicit operator writes)

Before a real pending upgrade, review both releases and every migration. Use
Expand → Migrate → Contract: add compatible fields/tables first; make old/new
code coexist; migrate data; observe stability; remove old structures only in a
later reviewed release. Pay particular attention to users, conversations,
messages, user_sessions and email_login_challenges. Revision ancestry is not
evidence of application compatibility. The apply compatibility flag is an
explicit operator approval bound to the saved release pair, not an automated
compatibility claim.

Inventory Cron and all independent writers, arrange a window where none can
start, and wait for any daily report process to finish. Then manually stop only
LabAgent services (these two commands require sudo):

```bash
sudo systemctl stop labagent-web.service
sudo systemctl stop labagent-auth.service
pgrep -af 'generate_daily_report.py' || true
bash "$RELEASE/scripts/backup_beta_postgres.sh"
```

Do not proceed if any writer remains or might be launched during migration.
Backups must be made **after** the saved plan and after writer quiescence.
Record the exact new dump path as `$BACKUP`. Ensure postgres.env and labagent.env
refer to the same intended instance/database; C3 does not source postgres.env.

### Backup verification (read-only DB/files; Docker access required)

```bash
BACKUP=/data/zmr/projects/labagent_backups/postgres/<new-backup>.dump
"$PYTHON" "$RELEASE/scripts/migrate_beta_schema.py" verify-backup \
  --expected-db labagent_beta --expected-host 127.0.0.1 --expected-port 55433 \
  --target-revision "$TARGET" --plan "$PLAN" --backup "$BACKUP"
```

It requires a nonempty file directly under LABAGENT_POSTGRES_BACKUP_DIR
(default /data/zmr/projects/labagent_backups/postgres), matching SHA sidecar,
manifest name/size/revision/database, and a UTC timestamp no older than one hour,
not in the future and after plan creation. It reuses container pg_restore --list
without restoring anything. No backup is copied into the release or Git.

Checksums prove integrity, not provenance. Same-name DB/revision metadata cannot
prove which server produced a dump, nor that an operator has not edited the
sidecars. Trusted backup directory ownership, the A2 command, manual endpoint
review and a quiet maintenance window remain mandatory. No false provenance
guarantee is made.

### Explicit apply (DB writes; all LabAgent writers must be stopped)

Only for a real reviewed pending migration:

```bash
"$PYTHON" "$RELEASE/scripts/migrate_beta_schema.py" apply \
  --expected-db labagent_beta --expected-host 127.0.0.1 --expected-port 55433 \
  --target-revision "$TARGET" --plan "$PLAN" --backup "$BACKUP" \
  --authorize-upgrade --confirm-maintenance --approve-code-schema-compatibility
```

Apply repeats identity, revision, graph and backup checks; requires both systemd
ActiveState values to be inactive and no matching legacy/report process. These
read-only checks fail closed but cannot inventory every possible external DB
client. Approval confirms that the operator controlled the remaining writers.

A PostgreSQL session advisory lock is acquired on the exact connection supplied
to Alembic and held throughout the upgrade. C3 clients sharing this DB serialize;
direct raw Alembic or other non-cooperating tools do not honor this lock. Apply
sets a 10-second lock timeout and five-minute statement timeout. PostgreSQL DDL
often rolls back transactionally, but arbitrary migrations may commit or use
nontransactional statements. Never infer the actual schema solely from an error.

Mode-600 atomic JSON audit records are kept under shared logs/migrations, with
timestamp, release/Git identity, DB name, before/target/after revision, backup
filename/hash, approvals, and outcome. STARTED must be written before DDL.
Audit failure cannot yield reported success; if final logging fails, the schema
may already have changed. Inspect DB and audit manually. Records omit connection
URLs, credentials, raw driver messages, SQL and private user data.

### Post-migration verification and activation

Apply checks the final revision exactly. Independently run plan again (read-only
DB) to confirm UP_TO_DATE; use a **new** plan filename to preserve the original:

```bash
"$PYTHON" "$RELEASE/scripts/migrate_beta_schema.py" plan \
  --expected-db labagent_beta --expected-host 127.0.0.1 --expected-port 55433 \
  --target-revision "$TARGET" --plan "${PLAN%.json}-after.json"
bash "$RELEASE/scripts/activate_beta_release.sh" <candidate-release-id>
sudo systemctl start labagent-auth.service
sudo systemctl start labagent-web.service
bash /data/zmr/projects/labAgent_Server/scripts/verify_beta_release.sh
```

Complete browser login/chat/report acceptance, inspect journals, review migration
audit, then restore the agreed Cron/independent-task schedule. Activation and
service lifecycle remain explicit, separate operator writes.

## Failure runbook

- Preflight or backup failure: no DDL is allowed. Fix identity/configuration,
  graph or backup issues and create a fresh plan/backup when needed.
- Migration failure: keep writers stopped, retain backup and audit, inspect
  actual schema and revision with read-only tools. Partial DDL/data effects are
  possible. Review migration-specific remediation; never retry blindly.
- Migration succeeded but app failed: only use C1/C2 code rollback if the old
  release was reviewed as compatible with the current schema. Otherwise keep
  services stopped and investigate a reviewed forward fix or controlled recovery.
- Incompatible old code: verify the retained backup in a separate database using
  `restore_beta_postgres.sh --target-db <new-restore-test-db>`. Never default to
  restoring over production. A separately approved cutover/recovery plan must
  assess data lost since backup, replay requirements and extended downtime.

No C3 command executes downgrade, stamp, restore, service restart, Docker restart,
volume deletion, release activation/rollback or server restart. Operators must
not restart Ubuntu/Docker/PostgreSQL for this procedure. Existing rollback DBs
and backup assets are untouched. A code-only release with no schema change does
not require apply or a forced migration backup.

## Initial server acceptance with today's schema

First stage the C3 release normally, then run only plan against 0004 and expect
UP_TO_DATE. Review current/candidate release IDs and endpoint fields; test wrong
expected DB/port and confirm BLOCKED. Create a separate A2 backup if desired,
verify metadata/readability, inspect permissions and compare deployed revision.
Confirm C1/C2 stage/activate/rollback still contain no automatic migration.
Do not invent a production 0005 for acceptance. Real production upgrade waits
for the next meaningful, reviewed migration. Boot recovery remains Phase F.

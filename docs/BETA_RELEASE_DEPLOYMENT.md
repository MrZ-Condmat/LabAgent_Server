# Long-term Beta Release Deployment

Phase C1+C2 changes LabAgent deployment from in-place extraction to explicit,
immutable code releases. Deployment, activation, service restart, and health
verification are separate operator actions.

## Layout

```text
/data/zmr/projects/
├── labAgent_Server -> labagent_deploy/current
├── labagent_deploy/
│   ├── releases/
│   │   ├── 20260929_103512_abcdef0/
│   │   └── 20260929_110000_1234567/
│   ├── current  -> releases/<active-release>
│   └── previous -> releases/<previous-release>
├── labagent_shared/
│   ├── reports/
│   └── logs/
├── labagent_runtime/
│   ├── labagent.env
│   └── postgres.env
└── labagent_backups/
    └── postgres/
```

The compatibility path `/data/zmr/projects/labAgent_Server` remains stable for
systemd, Cron, runtime configuration, and existing absolute paths. It resolves
through `labagent_deploy/current` to the active release.

Each release contains committed code plus these shared links:

```text
reports -> /data/zmr/projects/labagent_shared/reports
logs    -> /data/zmr/projects/labagent_shared/logs
```

Reports and logs therefore survive activation and code rollback. Runtime
configuration, PostgreSQL, and backups remain outside every release.

The project-root write audit found `reports/` and `logs/` as the production
mutable directories: report generators write HTML/JSON below `reports/`, while
Cron and deployment metadata write below `logs/`. Python `__pycache__` and
`.pytest_cache` are temporary and are not shared state. No additional
production state directory currently requires cross-release persistence.

## Release manifest

Every successful staged release has `release.json` with exactly these
non-secret fields:

```text
release_id
full_git_sha
short_git_sha
created_at_utc
deployment_format_version
archive_sha256
```

It contains no environment values, database URL, password, API key, SMTP
credential, or authentication secret.

## Safety boundaries

- `deploy_to_server.ps1` packages tracked files from Git `HEAD`; untracked files
  are ignored. Modified or staged tracked files stop deployment.
- Stage extracts into `releases/.staging-<release-id>`, rejects unsafe archive
  paths and link entries, validates the release, then renames it to the final
  release directory.
- An existing release ID is never overwritten.
- Stage never changes `current` or `previous` and never restarts a service.
- Activation atomically replaces the `current` symlink. It does not restart a
  service; running Python processes continue using their already loaded code.
- Rollback swaps `current` and `previous`, so a second rollback toggles back to
  the release active before the first rollback.
- No release script invokes Alembic, modifies the database schema, restores a
  database, or operates the PostgreSQL container.
- Old releases are not automatically deleted.

Code rollback is safe only while the active and previous releases are
compatible with the current database schema. Schema-aware deployment and
rollback belong to Phase C3. A symlink rollback is not a database rollback.

## Normal deployment after bootstrap

Commit the intended local changes, then stage exactly Git `HEAD`:

```powershell
cd G:\labAgent\LabAgent_Server
git status
.\deploy_to_server.ps1
```

The command prints the release ID, full revision, and release directory. It
does not activate the release or affect the running application.

On the server, activate the printed release ID:

```bash
cd /data/zmr/projects/labAgent_Server
bash scripts/activate_beta_release.sh <release-id>
```

Activation validates the manifest, shared links, runtime configuration, and
database readiness; updates `previous`; atomically switches `current`; and
atomically records the manifest revision in shared
`logs/deployed_revision.txt`. It does not restart systemd.

During the explicit maintenance window, restart and verify:

```bash
sudo systemctl restart labagent-auth.service
sudo systemctl restart labagent-web.service
bash /data/zmr/projects/labAgent_Server/scripts/verify_beta_release.sh
```

Verification requires:

- a valid `current` release and compatibility symlink;
- valid shared reports/logs links and manifest;
- both systemd services active;
- HTTP 200 from FastAPI `/healthz`;
- HTTP 200 from Streamlit `/_stcore/health`;
- shared `deployed_revision.txt` matching the active manifest.

Complete a browser login and persistent chat check before declaring the
deployment accepted.

## Code rollback

If auth, Web, or browser validation fails:

```bash
cd /data/zmr/projects/labAgent_Server
bash scripts/rollback_beta_release.sh
sudo systemctl restart labagent-auth.service
sudo systemctl restart labagent-web.service
bash /data/zmr/projects/labAgent_Server/scripts/verify_beta_release.sh
```

Rollback changes code only. It does not roll back PostgreSQL, reports, logs,
runtime secrets, backups, or an Alembic revision.

## One-time migration from the live directory

The current server initially has a regular directory at
`/data/zmr/projects/labAgent_Server`. Perform this once during an approved
LabAgent maintenance window. No server restart or unrelated service restart is
required.

### 1. Stage the first release while the current services stay online

Run `deploy_to_server.ps1` locally and record its printed release ID. On the
server, confirm the release exists:

```bash
RELEASE_ID='<printed-release-id>'
RELEASE_DIR="/data/zmr/projects/labagent_deploy/releases/$RELEASE_ID"
test -f "$RELEASE_DIR/release.json"
```

### 2. Prepare and verify shared data copies

Create private shared directories owned by `zmr`:

```bash
install -d -m 755 /data/zmr/projects/labagent_shared
install -d -m 755 /data/zmr/projects/labagent_shared/reports
install -d -m 755 /data/zmr/projects/labagent_shared/logs
```

Make an initial online copy without deleting either source or destination:

```bash
rsync -aH /data/zmr/projects/labAgent_Server/reports/ \
  /data/zmr/projects/labagent_shared/reports/
rsync -aH /data/zmr/projects/labAgent_Server/logs/ \
  /data/zmr/projects/labagent_shared/logs/
```

Record source and destination counts and sizes:

```bash
find /data/zmr/projects/labAgent_Server/reports -type f | wc -l
find /data/zmr/projects/labagent_shared/reports -type f | wc -l
du -sb /data/zmr/projects/labAgent_Server/reports
du -sb /data/zmr/projects/labagent_shared/reports

find /data/zmr/projects/labAgent_Server/logs -type f | wc -l
find /data/zmr/projects/labagent_shared/logs -type f | wc -l
du -sb /data/zmr/projects/labAgent_Server/logs
du -sb /data/zmr/projects/labagent_shared/logs
```

### 3. Begin the short LabAgent-only maintenance window

Choose a window outside the scheduled daily report run and confirm no report
generator is active:

```bash
pgrep -af 'generate_daily_report.py' || true
```

If a report process is active, wait for it to finish before the final copy.

```bash
sudo systemctl stop labagent-web.service
sudo systemctl stop labagent-auth.service
```

Perform a final copy after writers have stopped, then require an empty checksum
dry-run before continuing:

```bash
rsync -aH /data/zmr/projects/labAgent_Server/reports/ \
  /data/zmr/projects/labagent_shared/reports/
rsync -aH /data/zmr/projects/labAgent_Server/logs/ \
  /data/zmr/projects/labagent_shared/logs/

rsync -aHnci /data/zmr/projects/labAgent_Server/reports/ \
  /data/zmr/projects/labagent_shared/reports/
rsync -aHnci /data/zmr/projects/labAgent_Server/logs/ \
  /data/zmr/projects/labagent_shared/logs/
```

Both dry-run commands must produce no file-change output. Recheck counts and
sizes before proceeding.

### 4. Activate and establish the compatibility symlink

Run activation directly from the staged release because the compatibility path
is still the old directory:

```bash
bash "$RELEASE_DIR/scripts/activate_beta_release.sh" "$RELEASE_ID"
```

Preserve the complete old live tree with a unique backup name. Do not delete
it:

```bash
BOOTSTRAP_TIME="$(date -u +%Y%m%d_%H%M%S)"
OLD_TREE="/data/zmr/projects/labAgent_Server.before_releases_$BOOTSTRAP_TIME"
mv /data/zmr/projects/labAgent_Server "$OLD_TREE"

ln -s /data/zmr/projects/labagent_deploy/current \
  /data/zmr/projects/labAgent_Server.new
mv -T /data/zmr/projects/labAgent_Server.new \
  /data/zmr/projects/labAgent_Server
```

Verify resolution before starting services:

```bash
readlink -f /data/zmr/projects/labAgent_Server
readlink -f /data/zmr/projects/labagent_deploy/current
```

Both commands must resolve to the same release directory.

### 5. Start and verify

```bash
sudo systemctl start labagent-auth.service
sudo systemctl start labagent-web.service
bash /data/zmr/projects/labAgent_Server/scripts/verify_beta_release.sh
```

Complete browser login, report viewing, and persistent chat checks. Keep
`$OLD_TREE` until real server validation and an agreed observation period have
completed.

### 6. Bootstrap rollback if the compatibility cutover fails

Stop only LabAgent services, remove only the compatibility symlink, and restore
the preserved tree:

```bash
sudo systemctl stop labagent-web.service
sudo systemctl stop labagent-auth.service
test -L /data/zmr/projects/labAgent_Server
unlink /data/zmr/projects/labAgent_Server
mv "$OLD_TREE" /data/zmr/projects/labAgent_Server
sudo systemctl start labagent-auth.service
sudo systemctl start labagent-web.service
```

Do not delete the staged releases or shared data while diagnosing the failed
cutover.

## Deferred work

Phase C1+C2 does not run `alembic upgrade`, `alembic downgrade`, or any database
migration. It does not add automatic production deployment, application
containers, automatic service restart, retention cleanup, or zero-downtime
deployment. Server boot recovery testing remains deferred to Phase F.

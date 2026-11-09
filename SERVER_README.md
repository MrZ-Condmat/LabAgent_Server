# LabAgent Server Deployment

This folder is prepared for Linux deployment at:

```bash
/data/zmr/projects/labAgent_Server
```

## Persistent Beta PostgreSQL

Long-term Beta PostgreSQL uses `compose.beta-postgres.yml`, the local
`postgres:15-alpine` image, and the stable named volume
`labagent_postgres_data`. It binds only to `127.0.0.1:55432` by default and
uses server-only credentials from
`/data/zmr/projects/labagent_runtime/postgres.env`.

```bash
bash scripts/manage_beta_postgres.sh start
bash scripts/manage_beta_postgres.sh health
bash scripts/manage_beta_postgres.sh status
bash scripts/manage_beta_postgres.sh stop
```

See [docs/BETA_POSTGRES.md](docs/BETA_POSTGRES.md) for provisioning, Alembic
initialization, persistence verification, and safety rules. In particular,
never use `docker compose down -v` for the Beta database.

Create a logical custom-format backup and perform a restore drill in a separate
database with:

```bash
bash scripts/backup_beta_postgres.sh
bash scripts/restore_beta_postgres.sh /path/to/backup.dump \
  --target-db labagent_restore_test
```

Backups default to `/data/zmr/projects/labagent_backups/postgres` and include
SHA256 and non-sensitive JSON metadata. The restore script always rejects the
current Beta database as its target.

`compose.integration.yml` remains a separate PostgreSQL 16 database backed by
`tmpfs` for destructive integration tests. It is not a Beta data store.

## Persistent Application Runtime Configuration

Long-term Beta application settings belong in the server-only file:

```text
/data/zmr/projects/labagent_runtime/labagent.env
```

FastAPI, Streamlit, and daily reports must receive the same selection variable:

```bash
export LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env
python scripts/validate_runtime_config.py
```

The validator reports only field status and never prints secret values. Local
development continues to use the project `.env` when `LABAGENT_ENV_FILE` is
unset. See [docs/BETA_RUNTIME_CONFIG.md](docs/BETA_RUNTIME_CONFIG.md) for the
template, precedence, validation, permissions, and future systemd contract.

It includes source code, `.env`, and existing `reports/`. It intentionally does not include the Windows virtual environment, `.git`, or old local logs.

## First Setup With Conda

Copy the folder to the server, then run:

```bash
ssh -p 22 USER@SERVER_IP
cd /data/zmr/projects/labAgent_Server
chmod +x scripts/*.sh
chmod 600 .env
bash scripts/setup_server.sh
```

The scripts use conda without requiring manual `conda activate`. By default they create/use this conda environment:

```text
labagent_server
```

If your conda executable is not in `PATH`, `$HOME/miniconda3/bin/conda`, `$HOME/anaconda3/bin/conda`, or `/opt/conda/bin/conda`, set it explicitly:

```bash
export CONDA_EXE=/absolute/path/to/conda
bash scripts/setup_server.sh
```

To use a different environment name:

```bash
export LABAGENT_CONDA_ENV=my_labagent_env
bash scripts/setup_server.sh
```

## Generate Reports Once

Dry run that does not call external APIs or create reports:

```bash
conda run -n labagent_server python scripts/generate_daily_report.py --skip-arxiv --skip-journals --skip-highlights
```

If `conda` is not in `PATH`, use the full executable path, for example:

```bash
/home/USER/miniconda3/bin/conda run -n labagent_server python scripts/generate_daily_report.py --skip-arxiv --skip-journals --skip-highlights
```

Normal run:

```bash
bash scripts/run_daily_report.sh
```

Logs are appended to:

```bash
/data/zmr/projects/labAgent_Server/logs/daily_report.log
```

## Cron Example

Run every day at 10:00 China time. The server and Cron use `Asia/Shanghai`:

```cron
0 10 * * * LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env /usr/bin/bash /data/zmr/projects/labAgent_Server/scripts/run_daily_report.sh
```

If cron cannot find conda, use `CONDA_EXE` inline:

```cron
0 10 * * * LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env CONDA_EXE=/home/zmr/miniforge3/bin/conda /usr/bin/bash /data/zmr/projects/labAgent_Server/scripts/run_daily_report.sh
```

The script defaults `LABAGENT_ENV_FILE` to the canonical server runtime file
above and preserves an explicitly selected override. Cron does not inherit the
Web service's environment. The script sets `TZ=Asia/Shanghai`; application code
reads `DEFAULT_TIMEZONE` from the selected runtime file.

Both scoring and chat use `DeepSeek-V4.1-Flash` in
`lab_agent/config/models.json`. Keep model-name changes committed in the release
source so a later deployment cannot restore obsolete model names. API keys stay
in the server-only runtime file.

For repairing saved scores or recovering missing dated reports, see
[`docs/REPORT_BACKFILL.md`](docs/REPORT_BACKFILL.md).

## Web App By IP And Port

Start Streamlit:

```bash
cd /data/zmr/projects/labAgent_Server
bash scripts/run_web_app.sh
```

By default it listens on:

```text
0.0.0.0:8501
```

Other users can visit:

```text
http://SERVER_IP:8501
```

This works only if the server firewall/security group allows TCP port `8501` and the other devices can reach the server IP. SSH port `22` is only for login/file transfer. Direct IP+port has no built-in access control; for public internet access, put it behind Nginx/HTTPS/authentication or restrict it to an intranet/VPN.

## Files Created After Deployment

Inside this project folder:

- `logs/daily_report.log`: created/appended by `scripts/run_daily_report.sh`.
- New report files under `reports/...`: created by daily report generation.
- Python runtime caches such as `__pycache__/`: may appear when Python imports modules.
- Streamlit runtime state may appear under `.streamlit/` only if you later add Streamlit config.

Outside this project folder:

- The conda environment is created wherever your conda installation stores environments, commonly `~/miniconda3/envs/labagent_server`, `~/anaconda3/envs/labagent_server`, or another configured conda envs directory.
- Pip/conda package caches may be written under user cache folders such as `~/.cache/pip` and conda package/cache directories.
- Cron itself does not create project files, except the project log file written by the script.

## Useful Environment Values

The canonical private runtime file
`/data/zmr/projects/labagent_runtime/labagent.env` contains values such as:

```bash
LABAGENT_PROJECT_ROOT=/data/zmr/projects/labAgent_Server
STREAMLIT_HOST=0.0.0.0
STREAMLIT_PORT=8501
DEFAULT_TIMEZONE=Asia/Shanghai
```

Keep that external file private because it contains credentials and API keys.
Release archives never contain it.

## Stage a release from local Git

For releases with a real schema change, follow `docs/BETA_SCHEMA_MIGRATION.md`:
candidate-release read-only plan, explicit maintenance and new verified backup,
operator-authorized apply, revision verification, then activation. Code-only
releases do not require a forced migration. No deployment command runs Alembic.

After committing local changes, run:

```powershell
cd G:\labAgent\LabAgent_Server
git status
.\deploy_to_server.ps1
```

The deployment command packages tracked files from Git `HEAD`, permits
untracked local files because they cannot enter `git archive`, rejects modified
or staged tracked files, and stages an immutable release below:

```text
/data/zmr/projects/labagent_deploy/releases/<release-id>
```

It does not overwrite the live project, activate the release, stop or restart
services, run a smoke report, install dependencies, or call Alembic. The old
`-RestartWeb`, `-InstallDeps`, `-RunSmokeTest`, and `-AllowDirty` modes are
rejected before network operations.

After staging, activate and restart explicitly on the server:

```bash
cd /data/zmr/projects/labAgent_Server
bash scripts/activate_beta_release.sh <release-id>
sudo systemctl restart labagent-auth.service
sudo systemctl restart labagent-web.service
bash scripts/verify_beta_release.sh
```

Release layout, first-time migration, data-copy validation, rollback, and
database compatibility boundaries are documented in
`docs/BETA_RELEASE_DEPLOYMENT.md`.

## Long-term Beta systemd services

The repository includes system-level unit templates for the FastAPI auth
gateway and Streamlit Web application:

```text
config/systemd/labagent-auth.service
config/systemd/labagent-web.service
```

Both run as `zmr`, load the canonical B1 configuration through
`LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env`, validate
configuration, wait for PostgreSQL with `SELECT 1`, restart after failure, and
write logs to journald. Install and enable them without starting services:

```bash
cd /data/zmr/projects/labAgent_Server
sudo bash scripts/install_beta_systemd.sh
```

After installation and the one-time release-layout bootstrap, stage and
activate code, then explicitly restart the services during the approved
maintenance window:

```powershell
.\deploy_to_server.ps1
```

```bash
sudo systemctl restart labagent-auth.service
sudo systemctl restart labagent-web.service
```

The legacy `-RestartWeb` path is disabled for release deployment. systemd
installation and lifecycle are documented in `docs/BETA_SYSTEMD.md`; release
activation and rollback are documented in `docs/BETA_RELEASE_DEPLOYMENT.md`.
Server boot recovery validation remains deferred to Phase F.

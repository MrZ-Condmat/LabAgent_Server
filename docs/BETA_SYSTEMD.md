# Long-term Beta systemd Services

Beta Task B2 replaces manually supervised FastAPI and Streamlit `nohup`
processes with two system-level systemd services:

```text
labagent-auth.service
├── validate runtime configuration
├── wait for PostgreSQL with SELECT 1
└── Uvicorn on 0.0.0.0:8000

labagent-web.service
├── starts after and wants labagent-auth.service
├── validate runtime configuration
├── wait for PostgreSQL with SELECT 1
└── Streamlit on 0.0.0.0:8501
```

Both services run as `zmr:zmr`, use
`/data/zmr/projects/labAgent_Server` as their working directory, restart after
failure, and write stdout and stderr to journald. They do not contain database,
SMTP, API, or authentication secrets.

## Files and configuration

Repository templates:

```text
config/systemd/labagent-auth.service
config/systemd/labagent-web.service
```

Installed units:

```text
/etc/systemd/system/labagent-auth.service
/etc/systemd/system/labagent-web.service
```

Both units set only the path to the private B1 application configuration:

```text
LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env
```

They deliberately do not use systemd `EnvironmentFile=`. Python loads the file
with the same B1 dotenv rules used by FastAPI, Streamlit, daily reports, and the
configuration validator. PostgreSQL container configuration remains separate
in `postgres.env`.

## Pre-start checks

Each service runs these commands before starting the application:

```text
scripts/validate_runtime_config.py
scripts/wait_for_database.py
```

The database helper loads the selected runtime configuration, creates a
short-lived SQLAlchemy engine, and executes `SELECT 1`. It retries every two
seconds for up to 60 seconds. It reports only readiness or a generic timeout;
it never prints the database URL, password, or connection exception.

The timing can be adjusted for a manual diagnostic run:

```bash
LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env \
  /data/zmr/conda_envs/labagent_server/bin/python \
  scripts/wait_for_database.py --timeout 60 --interval 2
```

The units want and start after `network-online.target` and `docker.service`.
They do not start the PostgreSQL container. Persistent PostgreSQL lifecycle
continues to belong to `compose.beta-postgres.yml` and its management script.

## Install without switching processes

The installer must run with root privileges:

```bash
cd /data/zmr/projects/labAgent_Server
sudo bash scripts/install_beta_systemd.sh
```

It checks the project, private runtime file, executables, helpers, and unit
templates; installs the units with mode `0644`; reloads systemd; and enables
both services for a future boot. It does not start or restart a service, stop a
legacy process, use `pkill`, or restart the server.

## Manual cutover acceptance

Run this only during an approved service window. First validate the application
configuration and database without exposing their values:

```bash
cd /data/zmr/projects/labAgent_Server

LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env \
  /data/zmr/conda_envs/labagent_server/bin/python \
  scripts/validate_runtime_config.py

LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env \
  /data/zmr/conda_envs/labagent_server/bin/python \
  scripts/wait_for_database.py
```

Identify the existing manually launched processes before stopping them:

```bash
pgrep -af 'uvicorn.*lab_agent.api.app:app'
pgrep -af 'streamlit run lab_agent/web/app.py'
```

Stop only the verified legacy PIDs. The installer intentionally does not do
this. Then start and verify auth before starting Web:

```bash
sudo systemctl start labagent-auth.service
sudo systemctl status labagent-auth.service --no-pager
curl -fsS http://127.0.0.1:8000/healthz

sudo systemctl start labagent-web.service
sudo systemctl status labagent-web.service --no-pager
curl -fsS http://127.0.0.1:8501/_stcore/health
```

Complete a browser login and persistent chat check. Then exercise explicit
service restarts and repeat the health checks:

```bash
sudo systemctl restart labagent-auth.service
curl -fsS http://127.0.0.1:8000/healthz

sudo systemctl restart labagent-web.service
curl -fsS http://127.0.0.1:8501/_stcore/health
```

Inspect service logs:

```bash
sudo journalctl -u labagent-auth.service -n 100 --no-pager
sudo journalctl -u labagent-web.service -n 100 --no-pager
```

Server reboot testing is intentionally excluded from B2 because unrelated
compute workloads may be running. Enabled services are configured for
`multi-user.target`; boot recovery validation is deferred to Phase F.

## Normal lifecycle

Start:

```bash
sudo systemctl start labagent-auth.service
sudo systemctl start labagent-web.service
```

Stop in dependent-first order:

```bash
sudo systemctl stop labagent-web.service
sudo systemctl stop labagent-auth.service
```

Restart:

```bash
sudo systemctl restart labagent-auth.service
sudo systemctl restart labagent-web.service
```

Enable for a future boot:

```bash
sudo systemctl enable labagent-auth.service
sudo systemctl enable labagent-web.service
```

Status and logs:

```bash
sudo systemctl status labagent-auth.service --no-pager
sudo systemctl status labagent-web.service --no-pager
sudo journalctl -u labagent-auth.service -n 100 --no-pager
sudo journalctl -u labagent-web.service -n 100 --no-pager
```

## Deployment transition before Phase C

Once the units are installed, do not use:

```powershell
.\deploy_to_server.ps1 -RestartWeb
```

The deployment script detects the installed `labagent-web.service` and rejects
that legacy path before `pkill` or `nohup` runs. This prevents duplicate Web
processes and port conflicts.

Until Phase C adds systemd-aware atomic deployment, upload code without a
restart and explicitly restart both services on the server:

```powershell
.\deploy_to_server.ps1
```

```bash
sudo systemctl restart labagent-auth.service
sudo systemctl restart labagent-web.service
```

## Manual rollback

If service startup fails, inspect the journal first. A human operator can stop
both units and return temporarily to the previously verified clean-environment
commands:

```bash
sudo systemctl stop labagent-web.service
sudo systemctl stop labagent-auth.service
```

FastAPI fallback:

```bash
nohup env -i \
  HOME=/home/zmr \
  USER=zmr \
  PATH=/usr/bin:/bin \
  LABAGENT_PROJECT_ROOT=/data/zmr/projects/labAgent_Server \
  LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env \
  /data/zmr/conda_envs/labagent_server/bin/uvicorn \
  lab_agent.api.app:app --host 0.0.0.0 --port 8000 \
  > /tmp/labagent-auth.log 2>&1 < /dev/null &
```

Streamlit fallback:

```bash
cd /data/zmr/projects/labAgent_Server
nohup env -i \
  HOME=/home/zmr \
  USER=zmr \
  PATH=/usr/bin:/bin \
  LABAGENT_PROJECT_ROOT=/data/zmr/projects/labAgent_Server \
  LABAGENT_ENV_FILE=/data/zmr/projects/labagent_runtime/labagent.env \
  CONDA_EXE=/home/zmr/miniforge3/bin/conda \
  LABAGENT_CONDA_ENV=labagent_server \
  /usr/bin/bash scripts/run_web_app.sh \
  > /tmp/labagent-web.log 2>&1 < /dev/null &
```

Rollback remains an explicit operator action. The installer and unit files do
not kill processes, modify PostgreSQL data, or remove rollback assets.

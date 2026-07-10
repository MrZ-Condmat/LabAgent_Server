# LabAgent Server Deployment

This folder is prepared for Linux deployment at:

```bash
/data/zmr/projects/labAgent_Server
```

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

Run every day at 08:00 China time:

```cron
0 8 * * * /data/zmr/projects/labAgent_Server/scripts/run_daily_report.sh
```

If cron cannot find conda, use `CONDA_EXE` inline:

```cron
0 8 * * * CONDA_EXE=/home/USER/miniconda3/bin/conda /data/zmr/projects/labAgent_Server/scripts/run_daily_report.sh
```

The script sets `TZ=Asia/Shanghai`; code also reads `DEFAULT_TIMEZONE=Asia/Shanghai` from `.env`.

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

`.env` contains:

```bash
LABAGENT_PROJECT_ROOT=/data/zmr/projects/labAgent_Server
STREAMLIT_HOST=0.0.0.0
STREAMLIT_PORT=8501
DEFAULT_TIMEZONE=Asia/Shanghai
```

Keep `.env` private because it contains API keys.

## Deploy Local Changes To Server

After editing files locally in `G:\labAgent\labAgent_Server`, commit your work and deploy from Windows PowerShell:

```powershell
cd G:\labAgent\labAgent_Server
git status
git add .
git commit -m "Describe the change"
.\deploy_to_server.ps1 -RestartWeb -RunSmokeTest
```

The deploy script uploads code to:

```text
/data/zmr/projects/labAgent_Server
```

It does not upload or overwrite server runtime/private data:

```text
.env
reports/
logs/
.git/
.venv/
labagent/
__pycache__/
*.pyc
```

Useful options:

```powershell
.\deploy_to_server.ps1
.\deploy_to_server.ps1 -RestartWeb
.\deploy_to_server.ps1 -InstallDeps -RestartWeb
.\deploy_to_server.ps1 -RunSmokeTest -RestartWeb
.\deploy_to_server.ps1 -AllowDirty -RestartWeb
```

Use `-InstallDeps` after changing `requirements.txt`. Use `-AllowDirty` only when you intentionally want to deploy uncommitted local files.

#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/data/zmr/projects/labAgent_Server"
RUNTIME_ENV="/data/zmr/projects/labagent_runtime/labagent.env"
UNIT_SOURCE_DIR="$PROJECT_ROOT/config/systemd"
SYSTEMD_DIR="/etc/systemd/system"
PYTHON_BIN="/data/zmr/conda_envs/labagent_server/bin/python"
UVICORN_BIN="/data/zmr/conda_envs/labagent_server/bin/uvicorn"
CONDA_EXE="/home/zmr/miniforge3/bin/conda"

if [[ "$EUID" -ne 0 ]]; then
  echo "This installer must be run as root, for example with sudo." >&2
  exit 1
fi

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Required file not found: $path" >&2
    exit 1
  fi
}

require_executable() {
  local path="$1"
  if [[ ! -x "$path" ]]; then
    echo "Required executable not found or not executable: $path" >&2
    exit 1
  fi
}

if [[ ! -d "$PROJECT_ROOT" ]]; then
  echo "Project directory not found: $PROJECT_ROOT" >&2
  exit 1
fi

require_file "$RUNTIME_ENV"
require_file "$UNIT_SOURCE_DIR/labagent-auth.service"
require_file "$UNIT_SOURCE_DIR/labagent-web.service"
require_file "$PROJECT_ROOT/scripts/validate_runtime_config.py"
require_file "$PROJECT_ROOT/scripts/wait_for_database.py"
require_file "$PROJECT_ROOT/scripts/run_web_app.sh"
require_executable "$PYTHON_BIN"
require_executable "$UVICORN_BIN"
require_executable "$CONDA_EXE"

install -m 0644 "$UNIT_SOURCE_DIR/labagent-auth.service" \
  "$SYSTEMD_DIR/labagent-auth.service"
install -m 0644 "$UNIT_SOURCE_DIR/labagent-web.service" \
  "$SYSTEMD_DIR/labagent-web.service"

systemctl daemon-reload
systemctl enable labagent-auth.service labagent-web.service

echo "LabAgent systemd units installed and enabled."
echo "Services were not started; complete the documented manual cutover when ready."

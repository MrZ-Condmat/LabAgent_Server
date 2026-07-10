#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${LABAGENT_PROJECT_ROOT:-/data/zmr/projects/labAgent_Server}"
CONDA_ENV_NAME="${LABAGENT_CONDA_ENV:-labagent_server}"
cd "$PROJECT_ROOT"

export LABAGENT_PROJECT_ROOT="$PROJECT_ROOT"
export TZ="Asia/Shanghai"

find_conda() {
  if [[ -n "${CONDA_EXE:-}" && -x "${CONDA_EXE:-}" ]]; then
    echo "$CONDA_EXE"
    return 0
  fi
  if command -v conda >/dev/null 2>&1; then
    command -v conda
    return 0
  fi
  for candidate in "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" "/opt/conda/bin/conda"; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  echo "conda executable not found. Set CONDA_EXE=/path/to/conda and rerun." >&2
  return 1
}

mkdir -p logs
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  CONDA_BIN="$(find_conda)"
  "$CONDA_BIN" run -n "$CONDA_ENV_NAME" python scripts/generate_daily_report.py --skip-weekends
} >> logs/daily_report.log 2>&1
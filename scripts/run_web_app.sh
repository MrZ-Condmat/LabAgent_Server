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
  for candidate in "$HOME/miniforge3/bin/conda" "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" "/opt/conda/bin/conda"; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  echo "conda executable not found. Set CONDA_EXE=/path/to/conda and rerun." >&2
  return 1
}

CONDA_BIN="$(find_conda)"
if ! WEB_RUNTIME="$("$CONDA_BIN" run --no-capture-output -n "$CONDA_ENV_NAME" python scripts/resolve_web_runtime.py)"; then
  echo "Unable to resolve Streamlit runtime configuration." >&2
  exit 1
fi
IFS=$'\t' read -r HOST PORT <<< "$WEB_RUNTIME"
if [[ -z "$HOST" || ! "$PORT" =~ ^[0-9]+$ ]]; then
  echo "Invalid Streamlit runtime configuration." >&2
  exit 1
fi

exec "$CONDA_BIN" run --no-capture-output -n "$CONDA_ENV_NAME" python -m streamlit run lab_agent/web/app.py --server.address "$HOST" --server.port "$PORT"

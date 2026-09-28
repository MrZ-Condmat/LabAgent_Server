#!/usr/bin/env bash
set -euo pipefail

DEPLOY_ROOT="${LABAGENT_DEPLOY_ROOT:-/data/zmr/projects/labagent_deploy}"
SHARED_ROOT="${LABAGENT_SHARED_ROOT:-/data/zmr/projects/labagent_shared}"
RUNTIME_ENV="${LABAGENT_ENV_FILE:-/data/zmr/projects/labagent_runtime/labagent.env}"
PYTHON_BIN="${LABAGENT_PYTHON_BIN:-/data/zmr/conda_envs/labagent_server/bin/python}"
UVICORN_BIN="${LABAGENT_UVICORN_BIN:-/data/zmr/conda_envs/labagent_server/bin/uvicorn}"
CONDA_EXE="${CONDA_EXE:-/home/zmr/miniforge3/bin/conda}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE_HELPER="${RELEASE_ARCHIVE_HELPER:-$SCRIPT_DIR/release_archive.py}"

usage() {
  echo "Usage: $0 <release-archive.tar.gz> <release-id> <full-git-sha>" >&2
}

if [[ $# -ne 3 ]]; then
  usage
  exit 2
fi

ARCHIVE="$1"
RELEASE_ID="$2"
FULL_GIT_SHA="$3"

if [[ ! "$RELEASE_ID" =~ ^[0-9]{8}_[0-9]{6}_[0-9a-f]{7,12}$ ]]; then
  echo "Invalid release ID." >&2
  exit 2
fi
if [[ ! "$FULL_GIT_SHA" =~ ^[0-9a-f]{40,64}$ ]]; then
  echo "Invalid full Git revision." >&2
  exit 2
fi
SHORT_GIT_SHA="${RELEASE_ID##*_}"
if [[ "${FULL_GIT_SHA:0:${#SHORT_GIT_SHA}}" != "$SHORT_GIT_SHA" ]]; then
  echo "Release ID does not match the Git revision." >&2
  exit 2
fi

for required_file in "$ARCHIVE" "$RUNTIME_ENV" "$ARCHIVE_HELPER"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Required file not found: $required_file" >&2
    exit 1
  fi
done
for required_executable in "$PYTHON_BIN" "$UVICORN_BIN" "$CONDA_EXE"; do
  if [[ ! -x "$required_executable" ]]; then
    echo "Required executable not found: $required_executable" >&2
    exit 1
  fi
done
if ! command -v flock >/dev/null 2>&1; then
  echo "Required command not found: flock" >&2
  exit 1
fi

RELEASES_DIR="$DEPLOY_ROOT/releases"
FINAL_DIR="$RELEASES_DIR/$RELEASE_ID"
STAGING_DIR="$RELEASES_DIR/.staging-$RELEASE_ID"
STAGING_CREATED=0

cleanup() {
  if [[ "$STAGING_CREATED" -eq 1 && -d "$STAGING_DIR" && ! -L "$STAGING_DIR" ]]; then
    rm -rf -- "$STAGING_DIR"
  fi
}
trap cleanup EXIT

mkdir -p -- "$RELEASES_DIR" "$SHARED_ROOT/reports" "$SHARED_ROOT/logs"
if [[ -e "$FINAL_DIR" || -L "$FINAL_DIR" ]]; then
  echo "Release already exists and will not be overwritten: $RELEASE_ID" >&2
  exit 1
fi
if [[ -e "$STAGING_DIR" || -L "$STAGING_DIR" ]]; then
  echo "Staging path already exists; remove it only after operator review." >&2
  exit 1
fi

mkdir -- "$STAGING_DIR"
STAGING_CREATED=1
"$PYTHON_BIN" "$ARCHIVE_HELPER" safe-extract "$ARCHIVE" "$STAGING_DIR"

if [[ -e "$STAGING_DIR/release.json" || -L "$STAGING_DIR/release.json" ]]; then
  echo "Release archive must not provide release.json." >&2
  exit 1
fi
if [[ -e "$STAGING_DIR/reports" || -L "$STAGING_DIR/reports" || \
      -e "$STAGING_DIR/logs" || -L "$STAGING_DIR/logs" ]]; then
  echo "Release archive must not contain reports or logs." >&2
  exit 1
fi
if find "$STAGING_DIR" -type f \( -name '.env' -o -name 'labagent.env' -o -name 'postgres.env' \) -print -quit | grep -q .; then
  echo "Release archive contains a forbidden runtime environment file." >&2
  exit 1
fi

for required_path in \
  scripts/release_archive.py \
  scripts/validate_runtime_config.py \
  scripts/wait_for_database.py \
  scripts/run_web_app.sh \
  lab_agent/api/app.py; do
  if [[ ! -f "$STAGING_DIR/$required_path" ]]; then
    echo "Release is missing required file: $required_path" >&2
    exit 1
  fi
done

ln -s "$SHARED_ROOT/reports" "$STAGING_DIR/reports"
ln -s "$SHARED_ROOT/logs" "$STAGING_DIR/logs"
find "$STAGING_DIR/scripts" -maxdepth 1 -type f -name '*.sh' -exec chmod 0755 {} +

"$PYTHON_BIN" "$STAGING_DIR/scripts/release_archive.py" write-manifest \
  "$STAGING_DIR/release.json" "$RELEASE_ID" "$FULL_GIT_SHA" "$ARCHIVE"
"$PYTHON_BIN" "$STAGING_DIR/scripts/release_archive.py" validate-manifest \
  "$STAGING_DIR/release.json" "$RELEASE_ID"

PYTHONPYCACHEPREFIX="$STAGING_DIR/.compile-cache" \
  "$PYTHON_BIN" -m compileall -q "$STAGING_DIR/lab_agent" "$STAGING_DIR/scripts"
rm -rf -- "$STAGING_DIR/.compile-cache"

LABAGENT_PROJECT_ROOT="$STAGING_DIR" LABAGENT_ENV_FILE="$RUNTIME_ENV" \
  "$PYTHON_BIN" "$STAGING_DIR/scripts/validate_runtime_config.py"
LABAGENT_PROJECT_ROOT="$STAGING_DIR" LABAGENT_ENV_FILE="$RUNTIME_ENV" \
  "$PYTHON_BIN" "$STAGING_DIR/scripts/wait_for_database.py"

exec 9>"$DEPLOY_ROOT/.stage-release.lock"
flock 9
if [[ -e "$FINAL_DIR" || -L "$FINAL_DIR" ]]; then
  echo "Release appeared during staging and will not be overwritten: $RELEASE_ID" >&2
  exit 1
fi
mv -T -- "$STAGING_DIR" "$FINAL_DIR"
STAGING_CREATED=0

echo "STAGED_RELEASE_ID=$RELEASE_ID"
echo "STAGED_REVISION=$FULL_GIT_SHA"
echo "STAGED_RELEASE_DIR=$FINAL_DIR"

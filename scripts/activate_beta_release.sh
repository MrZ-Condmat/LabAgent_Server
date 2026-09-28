#!/usr/bin/env bash
set -euo pipefail

DEPLOY_ROOT="${LABAGENT_DEPLOY_ROOT:-/data/zmr/projects/labagent_deploy}"
SHARED_ROOT="${LABAGENT_SHARED_ROOT:-/data/zmr/projects/labagent_shared}"
RUNTIME_ENV="${LABAGENT_ENV_FILE:-/data/zmr/projects/labagent_runtime/labagent.env}"
PYTHON_BIN="${LABAGENT_PYTHON_BIN:-/data/zmr/conda_envs/labagent_server/bin/python}"
UVICORN_BIN="${LABAGENT_UVICORN_BIN:-/data/zmr/conda_envs/labagent_server/bin/uvicorn}"
CONDA_EXE="${CONDA_EXE:-/home/zmr/miniforge3/bin/conda}"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <release-id>" >&2
  exit 2
fi
RELEASE_ID="$1"
if [[ ! "$RELEASE_ID" =~ ^[0-9]{8}_[0-9]{6}_[0-9a-f]{7,12}$ ]]; then
  echo "Invalid release ID." >&2
  exit 2
fi

RELEASES_DIR="$DEPLOY_ROOT/releases"
RELEASE_DIR="$RELEASES_DIR/$RELEASE_ID"
CURRENT_LINK="$DEPLOY_ROOT/current"
PREVIOUS_LINK="$DEPLOY_ROOT/previous"
CURRENT_TMP="$DEPLOY_ROOT/.current-new-$$"
PREVIOUS_TMP="$DEPLOY_ROOT/.previous-new-$$"

cleanup() {
  rm -f -- "$CURRENT_TMP" "$PREVIOUS_TMP"
}
trap cleanup EXIT

if [[ ! -d "$RELEASE_DIR" || -L "$RELEASE_DIR" ]]; then
  echo "Release does not exist as an immutable directory: $RELEASE_ID" >&2
  exit 1
fi
for required_executable in "$PYTHON_BIN" "$UVICORN_BIN" "$CONDA_EXE"; do
  if [[ ! -x "$required_executable" ]]; then
    echo "Required executable not found: $required_executable" >&2
    exit 1
  fi
done
for shared_name in reports logs; do
  if [[ ! -L "$RELEASE_DIR/$shared_name" ]]; then
    echo "Release has no shared $shared_name symlink." >&2
    exit 1
  fi
  if [[ "$(readlink "$RELEASE_DIR/$shared_name")" != "$SHARED_ROOT/$shared_name" ]]; then
    echo "Release $shared_name symlink has an unexpected target." >&2
    exit 1
  fi
done

RELEASE_HELPER="$RELEASE_DIR/scripts/release_archive.py"
"$PYTHON_BIN" "$RELEASE_HELPER" validate-manifest \
  "$RELEASE_DIR/release.json" "$RELEASE_ID"
LABAGENT_PROJECT_ROOT="$RELEASE_DIR" LABAGENT_ENV_FILE="$RUNTIME_ENV" \
  "$PYTHON_BIN" "$RELEASE_DIR/scripts/validate_runtime_config.py"
LABAGENT_PROJECT_ROOT="$RELEASE_DIR" LABAGENT_ENV_FILE="$RUNTIME_ENV" \
  "$PYTHON_BIN" "$RELEASE_DIR/scripts/wait_for_database.py"

OLD_RELEASE_ID="none"
if [[ -L "$CURRENT_LINK" ]]; then
  OLD_CURRENT="$(readlink -f "$CURRENT_LINK")"
  case "$OLD_CURRENT" in
    "$RELEASES_DIR"/*) ;;
    *) echo "Current symlink points outside the releases directory." >&2; exit 1 ;;
  esac
  OLD_RELEASE_ID="$(basename "$OLD_CURRENT")"
  if [[ "$OLD_RELEASE_ID" == "$RELEASE_ID" ]]; then
    echo "Release is already active: $RELEASE_ID" >&2
    exit 1
  fi
  "$PYTHON_BIN" "$OLD_CURRENT/scripts/release_archive.py" validate-manifest \
    "$OLD_CURRENT/release.json" "$OLD_RELEASE_ID"
  if [[ -e "$PREVIOUS_LINK" && ! -L "$PREVIOUS_LINK" ]]; then
    echo "Previous path exists but is not a symlink." >&2
    exit 1
  fi
elif [[ -e "$CURRENT_LINK" ]]; then
  echo "Current path exists but is not a symlink." >&2
  exit 1
elif [[ -e "$PREVIOUS_LINK" || -L "$PREVIOUS_LINK" ]]; then
  echo "Previous exists without a current release; operator review is required." >&2
  exit 1
fi

ln -s "releases/$RELEASE_ID" "$CURRENT_TMP"
if [[ "$OLD_RELEASE_ID" != "none" ]]; then
  ln -s "releases/$OLD_RELEASE_ID" "$PREVIOUS_TMP"
  mv -Tf -- "$PREVIOUS_TMP" "$PREVIOUS_LINK"
fi
mv -Tf -- "$CURRENT_TMP" "$CURRENT_LINK"

FULL_GIT_SHA="$("$PYTHON_BIN" "$RELEASE_HELPER" manifest-value \
  "$RELEASE_DIR/release.json" "$RELEASE_ID" full_git_sha)"
REVISION_TMP="$SHARED_ROOT/logs/.deployed_revision.txt.$$"
printf '%s\n' "$FULL_GIT_SHA" > "$REVISION_TMP"
chmod 0644 "$REVISION_TMP"
mv -f -- "$REVISION_TMP" "$SHARED_ROOT/logs/deployed_revision.txt"

echo "ACTIVATED_RELEASE_ID=$RELEASE_ID"
echo "PREVIOUS_RELEASE_ID=$OLD_RELEASE_ID"

#!/usr/bin/env bash
set -euo pipefail

DEPLOY_ROOT="${LABAGENT_DEPLOY_ROOT:-/data/zmr/projects/labagent_deploy}"
SHARED_ROOT="${LABAGENT_SHARED_ROOT:-/data/zmr/projects/labagent_shared}"
COMPAT_PATH="${LABAGENT_COMPAT_PATH:-/data/zmr/projects/labAgent_Server}"
PYTHON_BIN="${LABAGENT_PYTHON_BIN:-/data/zmr/conda_envs/labagent_server/bin/python}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
CURL_BIN="${CURL_BIN:-curl}"
RELEASES_DIR="$DEPLOY_ROOT/releases"
CURRENT_LINK="$DEPLOY_ROOT/current"

if [[ $# -ne 0 ]]; then
  echo "Usage: $0" >&2
  exit 2
fi
if [[ ! -L "$CURRENT_LINK" ]]; then
  echo "Current release symlink is missing." >&2
  exit 1
fi

CURRENT_DIR="$(readlink -f "$CURRENT_LINK")"
case "$CURRENT_DIR" in
  "$RELEASES_DIR"/*) ;;
  *) echo "Current symlink points outside the releases directory." >&2; exit 1 ;;
esac
if [[ ! -d "$CURRENT_DIR" || -L "$CURRENT_DIR" ]]; then
  echo "Current release directory is invalid." >&2
  exit 1
fi
RELEASE_ID="$(basename "$CURRENT_DIR")"
RELEASE_HELPER="$CURRENT_DIR/scripts/release_archive.py"
"$PYTHON_BIN" "$RELEASE_HELPER" validate-manifest \
  "$CURRENT_DIR/release.json" "$RELEASE_ID"

for shared_name in reports logs; do
  if [[ ! -L "$CURRENT_DIR/$shared_name" ]] || \
     [[ "$(readlink "$CURRENT_DIR/$shared_name")" != "$SHARED_ROOT/$shared_name" ]]; then
    echo "Current release has an invalid shared $shared_name symlink." >&2
    exit 1
  fi
done

if [[ ! -L "$COMPAT_PATH" ]] || [[ "$(readlink -f "$COMPAT_PATH")" != "$CURRENT_DIR" ]]; then
  echo "Compatibility project path does not resolve to the current release." >&2
  exit 1
fi

"$SYSTEMCTL_BIN" is-active --quiet labagent-auth.service
"$SYSTEMCTL_BIN" is-active --quiet labagent-web.service
"$CURL_BIN" -fsS --max-time 5 http://127.0.0.1:8000/healthz >/dev/null
"$CURL_BIN" -fsS --max-time 5 http://127.0.0.1:8501/_stcore/health >/dev/null

EXPECTED_REVISION="$("$PYTHON_BIN" "$RELEASE_HELPER" manifest-value \
  "$CURRENT_DIR/release.json" "$RELEASE_ID" full_git_sha)"
if [[ ! -f "$SHARED_ROOT/logs/deployed_revision.txt" ]]; then
  echo "Deployed revision record is missing." >&2
  exit 1
fi
RECORDED_REVISION="$(tr -d '\r\n' < "$SHARED_ROOT/logs/deployed_revision.txt")"
if [[ "$RECORDED_REVISION" != "$EXPECTED_REVISION" ]]; then
  echo "Deployed revision does not match the active release manifest." >&2
  exit 1
fi

echo "Active release verified: $RELEASE_ID"

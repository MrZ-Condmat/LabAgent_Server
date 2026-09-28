#!/usr/bin/env bash
set -euo pipefail

DEPLOY_ROOT="${LABAGENT_DEPLOY_ROOT:-/data/zmr/projects/labagent_deploy}"
SHARED_ROOT="${LABAGENT_SHARED_ROOT:-/data/zmr/projects/labagent_shared}"
PYTHON_BIN="${LABAGENT_PYTHON_BIN:-/data/zmr/conda_envs/labagent_server/bin/python}"
RELEASES_DIR="$DEPLOY_ROOT/releases"
CURRENT_LINK="$DEPLOY_ROOT/current"
PREVIOUS_LINK="$DEPLOY_ROOT/previous"
CURRENT_TMP="$DEPLOY_ROOT/.rollback-current-$$"
PREVIOUS_TMP="$DEPLOY_ROOT/.rollback-previous-$$"

if [[ $# -ne 0 ]]; then
  echo "Usage: $0" >&2
  exit 2
fi

cleanup() {
  rm -f -- "$CURRENT_TMP" "$PREVIOUS_TMP"
}
trap cleanup EXIT

release_from_link() {
  local link_path="$1"
  local label="$2"
  if [[ ! -L "$link_path" ]]; then
    echo "$label release symlink is missing." >&2
    return 1
  fi
  local resolved
  resolved="$(readlink -f "$link_path")"
  case "$resolved" in
    "$RELEASES_DIR"/*) ;;
    *) echo "$label symlink points outside the releases directory." >&2; return 1 ;;
  esac
  if [[ ! -d "$resolved" || -L "$resolved" ]]; then
    echo "$label release directory is invalid." >&2
    return 1
  fi
  printf '%s\n' "$resolved"
}

CURRENT_DIR="$(release_from_link "$CURRENT_LINK" Current)"
ROLLBACK_DIR="$(release_from_link "$PREVIOUS_LINK" Previous)"
CURRENT_ID="$(basename "$CURRENT_DIR")"
ROLLBACK_ID="$(basename "$ROLLBACK_DIR")"
if [[ "$CURRENT_ID" == "$ROLLBACK_ID" ]]; then
  echo "Current and previous refer to the same release." >&2
  exit 1
fi

for release_pair in "$CURRENT_DIR:$CURRENT_ID" "$ROLLBACK_DIR:$ROLLBACK_ID"; do
  release_dir="${release_pair%%:*}"
  release_id="${release_pair##*:}"
  helper="$release_dir/scripts/release_archive.py"
  "$PYTHON_BIN" "$helper" validate-manifest "$release_dir/release.json" "$release_id"
  for shared_name in reports logs; do
    if [[ ! -L "$release_dir/$shared_name" ]] || \
       [[ "$(readlink "$release_dir/$shared_name")" != "$SHARED_ROOT/$shared_name" ]]; then
      echo "Release $release_id has an invalid shared $shared_name symlink." >&2
      exit 1
    fi
  done
done

ln -s "releases/$ROLLBACK_ID" "$CURRENT_TMP"
ln -s "releases/$CURRENT_ID" "$PREVIOUS_TMP"
mv -Tf -- "$CURRENT_TMP" "$CURRENT_LINK"
mv -Tf -- "$PREVIOUS_TMP" "$PREVIOUS_LINK"

ROLLBACK_HELPER="$ROLLBACK_DIR/scripts/release_archive.py"
FULL_GIT_SHA="$("$PYTHON_BIN" "$ROLLBACK_HELPER" manifest-value \
  "$ROLLBACK_DIR/release.json" "$ROLLBACK_ID" full_git_sha)"
REVISION_TMP="$SHARED_ROOT/logs/.deployed_revision.txt.$$"
printf '%s\n' "$FULL_GIT_SHA" > "$REVISION_TMP"
chmod 0644 "$REVISION_TMP"
mv -f -- "$REVISION_TMP" "$SHARED_ROOT/logs/deployed_revision.txt"

echo "ROLLED_BACK_TO_RELEASE_ID=$ROLLBACK_ID"
echo "PREVIOUS_RELEASE_ID=$CURRENT_ID"

#!/usr/bin/env bash
set -euo pipefail
umask 077

ENV_FILE="${LABAGENT_POSTGRES_ENV_FILE:-/data/zmr/projects/labagent_runtime/postgres.env}"
CONTAINER="labagent-postgres"
BACKUP_FILE=""
TARGET_DB=""
REPLACE_EXISTING=0
CREATED_TARGET=0
POSTGRES_DB=""
POSTGRES_USER=""
POSTGRES_PASSWORD=""

usage() {
  echo "Usage: $0 BACKUP_FILE --target-db DATABASE [--replace-existing]" >&2
}

fail() {
  echo "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

read_env_value() {
  local key="$1"
  sed -n "s/^[[:space:]]*${key}=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r'
}

load_environment() {
  [[ -f "$ENV_FILE" ]] || fail "PostgreSQL environment file not found: $ENV_FILE"

  POSTGRES_DB="$(read_env_value POSTGRES_DB)"
  POSTGRES_USER="$(read_env_value POSTGRES_USER)"
  POSTGRES_PASSWORD="$(read_env_value POSTGRES_PASSWORD)"

  [[ -n "$POSTGRES_DB" ]] || fail "Required PostgreSQL setting is missing or empty: POSTGRES_DB"
  [[ -n "$POSTGRES_USER" ]] || fail "Required PostgreSQL setting is missing or empty: POSTGRES_USER"
  [[ -n "$POSTGRES_PASSWORD" ]] || fail "Required PostgreSQL setting is missing or empty: POSTGRES_PASSWORD"
  [[ "$POSTGRES_PASSWORD" != "CHANGE_ME" ]] || fail "POSTGRES_PASSWORD still contains the example placeholder."
  [[ "$POSTGRES_DB" =~ ^[A-Za-z0-9_]+$ ]] || fail "POSTGRES_DB contains unsupported characters."
}

check_container() {
  docker inspect "$CONTAINER" >/dev/null 2>&1 || fail "Beta PostgreSQL container does not exist: $CONTAINER"
  [[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER")" == "true" ]] || fail "Beta PostgreSQL container is not running."

  local health
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER")"
  [[ "$health" == "healthy" ]] || fail "Beta PostgreSQL container is not healthy (status: $health)."
  docker exec "$CONTAINER" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1 || fail "Beta PostgreSQL is not ready."
}

target_exists() {
  [[ "$(docker exec "$CONTAINER" psql -U "$POSTGRES_USER" -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname = '$TARGET_DB';")" == "1" ]]
}

cleanup_failed_restore() {
  local status=$?
  if (( status != 0 && CREATED_TARGET == 1 )); then
    echo "Restore failed; removing only the newly created target database: $TARGET_DB" >&2
    docker exec "$CONTAINER" dropdb -U "$POSTGRES_USER" --if-exists "$TARGET_DB" >/dev/null 2>&1 || \
      echo "Could not remove failed restore target; remove it manually after confirming its name." >&2
  fi
  exit "$status"
}
trap cleanup_failed_restore EXIT

[[ $# -ge 1 ]] || { usage; exit 2; }
BACKUP_FILE="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-db)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      TARGET_DB="$2"
      shift 2
      ;;
    --replace-existing)
      REPLACE_EXISTING=1
      shift
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

[[ -n "$TARGET_DB" ]] || fail "--target-db is required."
[[ "$TARGET_DB" =~ ^[A-Za-z0-9_]+$ ]] || fail "Target database name must match [A-Za-z0-9_]+."
[[ -f "$BACKUP_FILE" && -s "$BACKUP_FILE" ]] || fail "Backup file is missing or empty: $BACKUP_FILE"

require_command docker
require_command sha256sum
load_environment
[[ "$TARGET_DB" != "$POSTGRES_DB" ]] || fail "Refusing to restore over the source Beta database."
check_container

if ! docker exec -i "$CONTAINER" pg_restore --list < "$BACKUP_FILE" >/dev/null; then
  fail "Backup file is not a readable PostgreSQL custom archive."
fi

SHA_FILE="$BACKUP_FILE.sha256"
if [[ -f "$SHA_FILE" ]]; then
  EXPECTED_SHA256="$(awk 'NR == 1 {print $1}' "$SHA_FILE")"
  [[ "$EXPECTED_SHA256" =~ ^[A-Fa-f0-9]{64}$ ]] || fail "Backup SHA256 sidecar is invalid: $SHA_FILE"
  ACTUAL_SHA256="$(sha256sum "$BACKUP_FILE" | awk '{print $1}')"
  [[ "$ACTUAL_SHA256" == "$EXPECTED_SHA256" ]] || fail "Backup SHA256 verification failed."
  echo "SHA256 verification: passed"
else
  echo "Warning: no SHA256 sidecar found; archive validation passed." >&2
fi

if target_exists; then
  if (( REPLACE_EXISTING == 0 )); then
    fail "Target database already exists; choose another name or use --replace-existing."
  fi
  docker exec "$CONTAINER" dropdb -U "$POSTGRES_USER" "$TARGET_DB"
fi

docker exec "$CONTAINER" createdb -U "$POSTGRES_USER" -T template0 "$TARGET_DB"
CREATED_TARGET=1

docker exec -i "$CONTAINER" pg_restore -U "$POSTGRES_USER" -d "$TARGET_DB" \
  --no-owner --no-privileges --exit-on-error < "$BACKUP_FILE"

ALEMBIC_REVISION="$(docker exec "$CONTAINER" psql -U "$POSTGRES_USER" -d "$TARGET_DB" -Atqc 'SELECT version_num FROM alembic_version LIMIT 1')"
[[ -n "$ALEMBIC_REVISION" ]] || fail "Restored database has no Alembic revision."
echo "Alembic revision: $ALEMBIC_REVISION"

for table in users conversations messages; do
  TABLE_EXISTS="$(docker exec "$CONTAINER" psql -U "$POSTGRES_USER" -d "$TARGET_DB" -Atqc "SELECT to_regclass('public.$table') IS NOT NULL;")"
  [[ "$TABLE_EXISTS" == "t" ]] || fail "Required restored table is missing: $table"
  ROW_COUNT="$(docker exec "$CONTAINER" psql -U "$POSTGRES_USER" -d "$TARGET_DB" -Atqc "SELECT count(*) FROM $table;")"
  [[ "$ROW_COUNT" =~ ^[0-9]+$ ]] || fail "Could not verify row count for restored table: $table"
  echo "$table: $ROW_COUNT"
done

CREATED_TARGET=0
echo "Restore completed in separate database: $TARGET_DB"
echo "Source Beta database was not modified: $POSTGRES_DB"

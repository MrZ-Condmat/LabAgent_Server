#!/usr/bin/env bash
set -euo pipefail
umask 077

ENV_FILE="${LABAGENT_POSTGRES_ENV_FILE:-/data/zmr/projects/labagent_runtime/postgres.env}"
BACKUP_DIR="${LABAGENT_POSTGRES_BACKUP_DIR:-/data/zmr/projects/labagent_backups/postgres}"
CONTAINER="labagent-postgres"

TEMP_DUMP=""
TEMP_SHA=""
TEMP_MANIFEST=""

cleanup_temporary_files() {
  [[ -z "$TEMP_DUMP" ]] || rm -f -- "$TEMP_DUMP"
  [[ -z "$TEMP_SHA" ]] || rm -f -- "$TEMP_SHA"
  [[ -z "$TEMP_MANIFEST" ]] || rm -f -- "$TEMP_MANIFEST"
}
trap cleanup_temporary_files EXIT

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

require_command docker
require_command sha256sum
load_environment
check_container

mkdir -p -- "$BACKUP_DIR"
chmod 700 -- "$BACKUP_DIR"
[[ -d "$BACKUP_DIR" && -w "$BACKUP_DIR" ]] || fail "Backup directory is not writable: $BACKUP_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BACKUP_BASENAME="${POSTGRES_DB}_${TIMESTAMP}.dump"
BACKUP_FILE="$BACKUP_DIR/$BACKUP_BASENAME"
SHA_FILE="$BACKUP_FILE.sha256"
MANIFEST_FILE="$BACKUP_FILE.json"

[[ ! -e "$BACKUP_FILE" && ! -e "$SHA_FILE" && ! -e "$MANIFEST_FILE" ]] || fail "Backup destination already exists: $BACKUP_FILE"

TEMP_DUMP="$(mktemp "$BACKUP_DIR/.${BACKUP_BASENAME}.tmp.XXXXXX")"
TEMP_SHA="$(mktemp "$BACKUP_DIR/.${BACKUP_BASENAME}.sha256.tmp.XXXXXX")"
TEMP_MANIFEST="$(mktemp "$BACKUP_DIR/.${BACKUP_BASENAME}.json.tmp.XXXXXX")"
chmod 600 -- "$TEMP_DUMP" "$TEMP_SHA" "$TEMP_MANIFEST"

if ! docker exec "$CONTAINER" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$TEMP_DUMP"; then
  fail "PostgreSQL backup failed; incomplete output was removed."
fi
[[ -s "$TEMP_DUMP" ]] || fail "PostgreSQL backup is empty; incomplete output was removed."

if ! docker exec -i "$CONTAINER" pg_restore --list < "$TEMP_DUMP" >/dev/null; then
  fail "PostgreSQL backup archive validation failed; incomplete output was removed."
fi

BACKUP_SHA256="$(sha256sum "$TEMP_DUMP" | awk '{print $1}')"
BACKUP_SIZE="$(wc -c < "$TEMP_DUMP" | tr -d '[:space:]')"
ALEMBIC_REVISION="$(docker exec "$CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc 'SELECT version_num FROM alembic_version LIMIT 1' 2>/dev/null || true)"
[[ -n "$ALEMBIC_REVISION" ]] || ALEMBIC_REVISION="unknown"
POSTGRES_VERSION="$(docker exec "$CONTAINER" postgres --version 2>/dev/null || true)"
[[ -n "$POSTGRES_VERSION" ]] || POSTGRES_VERSION="unknown"

printf '%s  %s\n' "$BACKUP_SHA256" "$BACKUP_BASENAME" > "$TEMP_SHA"
printf '{\n  "database_name": "%s",\n  "created_at_utc": "%s",\n  "postgres_version": "%s",\n  "alembic_revision": "%s",\n  "backup_filename": "%s",\n  "backup_size_bytes": %s,\n  "sha256": "%s"\n}\n' \
  "$POSTGRES_DB" "$CREATED_AT" "$POSTGRES_VERSION" "$ALEMBIC_REVISION" \
  "$BACKUP_BASENAME" "$BACKUP_SIZE" "$BACKUP_SHA256" > "$TEMP_MANIFEST"

mv -- "$TEMP_DUMP" "$BACKUP_FILE"
TEMP_DUMP=""
mv -- "$TEMP_SHA" "$SHA_FILE"
TEMP_SHA=""
mv -- "$TEMP_MANIFEST" "$MANIFEST_FILE"
TEMP_MANIFEST=""
chmod 600 -- "$BACKUP_FILE" "$SHA_FILE" "$MANIFEST_FILE"

echo "Beta PostgreSQL backup completed: $BACKUP_FILE"
echo "Archive validation: passed"
echo "SHA256: $BACKUP_SHA256"
echo "Alembic revision: $ALEMBIC_REVISION"

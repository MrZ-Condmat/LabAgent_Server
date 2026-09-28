#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/compose.beta-postgres.yml"
ENV_FILE="${LABAGENT_POSTGRES_ENV_FILE:-/data/zmr/projects/labagent_runtime/postgres.env}"
SERVICE="postgres-beta"
CONTAINER="labagent-postgres"
IMAGE="postgres:15-alpine"

usage() {
  echo "Usage: $0 {start|stop|restart|status|logs|health}" >&2
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

read_env_value() {
  local key="$1"
  sed -n "s/^[[:space:]]*${key}=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r'
}

validate_environment() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "PostgreSQL environment file not found: $ENV_FILE" >&2
    exit 1
  fi

  local key value
  for key in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
    value="$(read_env_value "$key")"
    if [[ -z "$value" ]]; then
      echo "Required setting is missing or empty in PostgreSQL environment file: $key" >&2
      exit 1
    fi
    if [[ "$key" == "POSTGRES_PASSWORD" && "$value" == "CHANGE_ME" ]]; then
      echo "POSTGRES_PASSWORD still contains the example placeholder." >&2
      exit 1
    fi
  done

  value="$(read_env_value LABAGENT_POSTGRES_PORT)"
  if [[ -n "$value" ]] && { [[ ! "$value" =~ ^[0-9]+$ ]] || (( value < 1 || value > 65535 )); }; then
    echo "LABAGENT_POSTGRES_PORT must be an integer from 1 to 65535." >&2
    exit 1
  fi
}

check_tools() {
  require_command docker
  if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose is unavailable. Install the Docker Compose plugin." >&2
    exit 1
  fi
}

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

wait_until_healthy() {
  local attempt status
  for attempt in {1..60}; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null || true)"
    if [[ "$status" == "healthy" ]]; then
      echo "Beta PostgreSQL is healthy."
      return 0
    fi
    if [[ "$status" == "unhealthy" ]]; then
      echo "Beta PostgreSQL health check failed." >&2
      compose logs --tail 50 "$SERVICE" >&2
      return 1
    fi
    sleep 1
  done

  echo "Timed out waiting for Beta PostgreSQL health check." >&2
  compose logs --tail 50 "$SERVICE" >&2
  return 1
}

start_database() {
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Required local image is unavailable: $IMAGE" >&2
    echo "This script does not pull images automatically." >&2
    exit 1
  fi
  compose up -d --no-build --pull never "$SERVICE"
  wait_until_healthy
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

ACTION="$1"
case "$ACTION" in
  start|stop|restart|status|logs|health) ;;
  *)
    usage
    exit 2
    ;;
esac

check_tools
validate_environment

case "$ACTION" in
  start)
    start_database
    ;;
  stop)
    compose stop "$SERVICE"
    ;;
  restart)
    compose stop "$SERVICE"
    start_database
    ;;
  status)
    compose ps "$SERVICE"
    ;;
  logs)
    compose logs --tail 200 "$SERVICE"
    ;;
  health)
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null || true)"
    if [[ "$status" != "healthy" ]]; then
      echo "Beta PostgreSQL is not healthy (status: ${status:-not found})." >&2
      exit 1
    fi
    echo "Beta PostgreSQL is healthy."
    ;;
esac

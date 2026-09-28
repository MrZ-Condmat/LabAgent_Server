"""Static safety checks for persistent Beta PostgreSQL infrastructure."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
BETA_COMPOSE = (ROOT / "compose.beta-postgres.yml").read_text(encoding="utf-8")
INTEGRATION_COMPOSE = (ROOT / "compose.integration.yml").read_text(encoding="utf-8")
MANAGER = (ROOT / "scripts" / "manage_beta_postgres.sh").read_text(
    encoding="utf-8"
)
ENV_EXAMPLE = (ROOT / "config" / "postgres.env.example").read_text(
    encoding="utf-8"
)


def test_beta_compose_uses_local_postgres_15_without_pull():
    assert "image: postgres:15-alpine" in BETA_COMPOSE
    assert "pull_policy: never" in BETA_COMPOSE
    assert "image: postgres:16" not in BETA_COMPOSE


def test_beta_compose_uses_stable_persistent_volume_without_tmpfs():
    assert "tmpfs:" not in BETA_COMPOSE
    assert "labagent-postgres-data:/var/lib/postgresql/data" in BETA_COMPOSE
    assert re.search(r"(?m)^\s+name: labagent_postgres_data\s*$", BETA_COMPOSE)
    assert "container_name: labagent-postgres" in BETA_COMPOSE


def test_beta_compose_is_loopback_only_and_restartable():
    assert '"127.0.0.1:${LABAGENT_POSTGRES_PORT:-55432}:5432"' in BETA_COMPOSE
    assert "0.0.0.0:" not in BETA_COMPOSE
    assert "restart: unless-stopped" in BETA_COMPOSE


def test_beta_compose_has_credential_safe_healthcheck():
    assert "pg_isready" in BETA_COMPOSE
    assert "$${POSTGRES_USER}" in BETA_COMPOSE
    assert "$${POSTGRES_DB}" in BETA_COMPOSE
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:" in BETA_COMPOSE
    assert "DATABASE_URL" not in BETA_COMPOSE
    assert "CHANGE_ME" not in BETA_COMPOSE


def test_integration_database_remains_disposable_and_separate():
    assert "image: postgres:16" in INTEGRATION_COMPOSE
    assert "tmpfs:" in INTEGRATION_COMPOSE
    assert "/var/lib/postgresql/data" in INTEGRATION_COMPOSE
    assert "labagent_integration_test" in INTEGRATION_COMPOSE
    assert "labagent_postgres_data" not in INTEGRATION_COMPOSE
    assert "labagent-postgres" not in INTEGRATION_COMPOSE


def test_management_script_has_safe_lifecycle_only():
    assert "{start|stop|restart|status|logs|health}" in MANAGER
    assert "compose stop" in MANAGER
    assert "compose up -d --no-build --pull never" in MANAGER
    assert "docker image inspect" in MANAGER
    assert "down -v" not in MANAGER
    assert "volume rm" not in MANAGER
    assert not re.search(r"\b(destroy|reset)\)", MANAGER)


def test_management_script_uses_external_env_without_secret_output():
    assert "/data/zmr/projects/labagent_runtime/postgres.env" in MANAGER
    assert "LABAGENT_POSTGRES_ENV_FILE" in MANAGER
    assert '--env-file "$ENV_FILE"' in MANAGER
    assert '"$value" == "CHANGE_ME"' in MANAGER
    assert 'echo "$POSTGRES_PASSWORD"' not in MANAGER
    assert 'cat "$ENV_FILE"' not in MANAGER
    assert "miniforge" not in MANAGER.lower()
    assert "conda" not in MANAGER.lower()
    assert "lab_agent" not in MANAGER


def test_env_example_contains_placeholders_only():
    assert "POSTGRES_DB=labagent" in ENV_EXAMPLE
    assert "POSTGRES_USER=labagent" in ENV_EXAMPLE
    assert "POSTGRES_PASSWORD=CHANGE_ME" in ENV_EXAMPLE
    assert "LABAGENT_POSTGRES_PORT=55432" in ENV_EXAMPLE
    assert "postgresql+psycopg://" not in ENV_EXAMPLE

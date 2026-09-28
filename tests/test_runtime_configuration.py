"""Tests for external application runtime configuration selection and validation."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from lab_agent.utils import (
    Config,
    RuntimeConfigurationError,
    load_project_dotenv,
    resolve_project_dotenv_path,
)
from lab_agent.utils.runtime_validation import REQUIRED_RUNTIME_FIELDS


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_runtime_config.py"


@pytest.fixture(autouse=True)
def clear_runtime_selection(monkeypatch):
    monkeypatch.delenv("LABAGENT_ENV_FILE", raising=False)
    monkeypatch.delenv("RUNTIME_TEST_VALUE", raising=False)


def test_explicit_path_precedes_external_runtime_file(monkeypatch, tmp_path):
    explicit = tmp_path / "explicit.env"
    external = tmp_path / "external.env"
    explicit.write_text("RUNTIME_TEST_VALUE=explicit\n", encoding="utf-8")
    external.write_text("RUNTIME_TEST_VALUE=external\n", encoding="utf-8")
    monkeypatch.setenv("LABAGENT_ENV_FILE", str(external))

    selected = load_project_dotenv(explicit)

    assert selected == explicit.resolve()
    assert os.environ["RUNTIME_TEST_VALUE"] == "explicit"


def test_external_runtime_file_precedes_project_fallback(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("RUNTIME_TEST_VALUE=project\n", encoding="utf-8")
    external = tmp_path / "external.env"
    external.write_text("RUNTIME_TEST_VALUE=external\n", encoding="utf-8")
    monkeypatch.setenv("LABAGENT_PROJECT_ROOT", str(project))
    monkeypatch.setenv("LABAGENT_ENV_FILE", str(external))

    assert resolve_project_dotenv_path() == external.resolve()
    load_project_dotenv()
    assert os.environ["RUNTIME_TEST_VALUE"] == "external"


def test_missing_external_runtime_file_fails_clearly(monkeypatch, tmp_path):
    missing = tmp_path / "missing.env"
    monkeypatch.setenv("LABAGENT_ENV_FILE", str(missing))

    with pytest.raises(RuntimeConfigurationError, match="LABAGENT_ENV_FILE") as error:
        load_project_dotenv()

    assert str(missing) in str(error.value)


def test_project_env_remains_local_fallback(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    project_env = project / ".env"
    project_env.write_text("RUNTIME_TEST_VALUE=project\n", encoding="utf-8")
    monkeypatch.setenv("LABAGENT_PROJECT_ROOT", str(project))

    assert load_project_dotenv() == project_env.resolve()
    assert os.environ["RUNTIME_TEST_VALUE"] == "project"


def test_process_environment_precedes_dotenv(monkeypatch, tmp_path):
    runtime_file = tmp_path / "runtime.env"
    runtime_file.write_text("RUNTIME_TEST_VALUE=dotenv\n", encoding="utf-8")
    monkeypatch.setenv("LABAGENT_ENV_FILE", str(runtime_file))
    monkeypatch.setenv("RUNTIME_TEST_VALUE", "process")

    load_project_dotenv()

    assert os.environ["RUNTIME_TEST_VALUE"] == "process"


def test_external_runtime_file_supports_crlf(monkeypatch, tmp_path):
    runtime_file = tmp_path / "runtime.env"
    runtime_file.write_bytes(b"STREAMLIT_HOST=127.0.0.7\r\nSTREAMLIT_PORT=8765\r\n")
    monkeypatch.setenv("LABAGENT_ENV_FILE", str(runtime_file))
    monkeypatch.delenv("STREAMLIT_HOST", raising=False)
    monkeypatch.delenv("STREAMLIT_PORT", raising=False)

    config = Config()

    assert config.streamlit_host == "127.0.0.7"
    assert config.streamlit_port == 8765


def test_explicit_missing_path_preserves_previous_optional_behavior(tmp_path):
    missing = tmp_path / "optional-missing.env"
    assert load_project_dotenv(missing) == missing.resolve()


def _valid_runtime_values() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql+psycopg://labagent:testing-password@127.0.0.1:55432/labagent",
        "LABAGENT_ALLOWED_EMAIL_DOMAINS": "mails.tsinghua.edu.cn,mail.tsinghua.edu.cn",
        "AUTH_OTP_HMAC_SECRET": "a" * 40,
        "AUTH_SESSION_HMAC_SECRET": "b" * 40,
        "AUTH_COOKIE_NAME": "labagent_session",
        "AUTH_COOKIE_SECURE": "false",
        "LABAGENT_AUTH_GATEWAY_PUBLIC_URL": "http://127.0.0.1:8000",
        "LABAGENT_STREAMLIT_PUBLIC_URL": "http://127.0.0.1:8501",
        "SMTP_HOST": "mails.tsinghua.edu.cn",
        "SMTP_PORT": "465",
        "SMTP_USERNAME": "tester@mails.tsinghua.edu.cn",
        "SMTP_PASSWORD": "testing-app-password",
        "SMTP_FROM": "tester@mails.tsinghua.edu.cn",
        "SMTP_USE_SSL": "true",
        "DEEPSEEK_CHAT_API_KEY": "testing-chat-key",
        "DEEPSEEK_SCORING_API_KEY": "testing-scoring-key",
        "DEEPSEEK_BASE_URL": "https://llm.invalid/v1",
        "DEFAULT_TIMEZONE": "Asia/Shanghai",
        "STREAMLIT_HOST": "0.0.0.0",
        "STREAMLIT_PORT": "8501",
    }


def _run_validator(tmp_path: Path, changes: dict[str, str | None] | None = None):
    values = _valid_runtime_values()
    for name, value in (changes or {}).items():
        if value is None:
            values.pop(name, None)
        else:
            values[name] = value

    runtime_file = tmp_path / "labagent.env"
    runtime_file.write_text(
        "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    environment = {key: value for key, value in os.environ.items() if key not in REQUIRED_RUNTIME_FIELDS}
    environment["LABAGENT_ENV_FILE"] = str(runtime_file)
    return subprocess.run(
        [sys.executable, str(VALIDATOR)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_validator_accepts_valid_fake_configuration_without_secret_output(tmp_path):
    result = _run_validator(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "DATABASE_URL" in result.stdout and "SET" in result.stdout
    for secret in (
        "testing-password",
        "testing-app-password",
        "testing-chat-key",
        "testing-scoring-key",
        "a" * 40,
        "b" * 40,
    ):
        assert secret not in result.stdout
        assert secret not in result.stderr


@pytest.mark.parametrize(
    ("changes", "field", "status"),
    [
        ({"SMTP_PASSWORD": None}, "SMTP_PASSWORD", "MISSING"),
        ({"SMTP_PASSWORD": ""}, "SMTP_PASSWORD", "MISSING"),
        ({"AUTH_OTP_HMAC_SECRET": "<GENERATE_RANDOM_SECRET>"}, "AUTH_OTP_HMAC_SECRET", "INVALID"),
        ({"DEEPSEEK_CHAT_API_KEY": "<API_KEY>"}, "DEEPSEEK_CHAT_API_KEY", "INVALID"),
        ({"DATABASE_URL": "postgresql+psycopg://labagent:<PASSWORD>@localhost/labagent"}, "DATABASE_URL", "INVALID"),
        ({"AUTH_SESSION_HMAC_SECRET": "too-short"}, "AUTH_SESSION_HMAC_SECRET", "INVALID"),
        ({"STREAMLIT_PORT": "70000"}, "STREAMLIT_PORT", "INVALID"),
        ({"SMTP_PORT": "not-a-port"}, "SMTP_PORT", "INVALID"),
        ({"LABAGENT_AUTH_GATEWAY_PUBLIC_URL": "ftp://127.0.0.1"}, "LABAGENT_AUTH_GATEWAY_PUBLIC_URL", "INVALID"),
        ({"DEEPSEEK_BASE_URL": "file:///tmp/deepseek"}, "DEEPSEEK_BASE_URL", "INVALID"),
        ({"AUTH_COOKIE_SECURE": "sometimes"}, "AUTH_COOKIE_SECURE", "INVALID"),
        ({"DATABASE_URL": "sqlite:///labagent.db"}, "DATABASE_URL", "INVALID"),
    ],
)
def test_validator_rejects_invalid_or_missing_values(tmp_path, changes, field, status):
    result = _run_validator(tmp_path, changes)
    assert result.returncode != 0
    assert field in result.stdout
    assert status in next(line for line in result.stdout.splitlines() if line.startswith(field))


def test_fastapi_streamlit_and_daily_report_use_shared_loader():
    assert "return Config()" in (ROOT / "lab_agent/api/dependencies.py").read_text(encoding="utf-8")
    assert "load_project_dotenv()" in (ROOT / "lab_agent/web/app.py").read_text(encoding="utf-8")
    assert "load_project_dotenv()" in (ROOT / "scripts/generate_daily_report.py").read_text(encoding="utf-8")

"""Static safety and lifecycle checks for Long-term Beta systemd services."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
AUTH_UNIT = (ROOT / "config/systemd/labagent-auth.service").read_text(encoding="utf-8")
WEB_UNIT = (ROOT / "config/systemd/labagent-web.service").read_text(encoding="utf-8")
INSTALLER = (ROOT / "scripts/install_beta_systemd.sh").read_text(encoding="utf-8")
DEPLOYMENT = (ROOT / "deploy_to_server.ps1").read_text(encoding="utf-8-sig")

PROJECT_ROOT = "/data/zmr/projects/labAgent_Server"
RUNTIME_ENV = "/data/zmr/projects/labagent_runtime/labagent.env"
PYTHON = "/data/zmr/conda_envs/labagent_server/bin/python"


def _assert_common_service(unit: str) -> None:
    assert "Type=simple" in unit
    assert "User=zmr" in unit
    assert "Group=zmr" in unit
    assert f"WorkingDirectory={PROJECT_ROOT}" in unit
    assert f"Environment=LABAGENT_PROJECT_ROOT={PROJECT_ROOT}" in unit
    assert f"Environment=LABAGENT_ENV_FILE={RUNTIME_ENV}" in unit
    assert f"ExecStartPre={PYTHON} {PROJECT_ROOT}/scripts/validate_runtime_config.py" in unit
    assert f"ExecStartPre={PYTHON} {PROJECT_ROOT}/scripts/wait_for_database.py" in unit
    assert "Wants=network-online.target docker.service" in unit
    assert "After=network-online.target docker.service" in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=5" in unit
    assert "TimeoutStopSec=30" in unit
    assert "StandardOutput=journal" in unit
    assert "StandardError=journal" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "EnvironmentFile=" not in unit
    for secret_name in (
        "DATABASE_URL",
        "SMTP_PASSWORD",
        "AUTH_OTP_HMAC_SECRET",
        "AUTH_SESSION_HMAC_SECRET",
        "DEEPSEEK_CHAT_API_KEY",
        "DEEPSEEK_SCORING_API_KEY",
    ):
        assert secret_name not in unit


def test_auth_service_has_expected_uvicorn_lifecycle():
    _assert_common_service(AUTH_UNIT)
    assert "Description=LabAgent Auth Gateway" in AUTH_UNIT
    assert (
        "ExecStart=/data/zmr/conda_envs/labagent_server/bin/uvicorn "
        "lab_agent.api.app:app --host 0.0.0.0 --port 8000"
    ) in AUTH_UNIT


def test_web_service_reuses_launcher_and_orders_auth_first():
    _assert_common_service(WEB_UNIT)
    assert "Description=LabAgent Streamlit Web" in WEB_UNIT
    assert "Environment=CONDA_EXE=/home/zmr/miniforge3/bin/conda" in WEB_UNIT
    assert "Environment=LABAGENT_CONDA_ENV=labagent_server" in WEB_UNIT
    assert f"ExecStart=/usr/bin/bash {PROJECT_ROOT}/scripts/run_web_app.sh" in WEB_UNIT
    assert "Wants=network-online.target docker.service labagent-auth.service" in WEB_UNIT
    assert "After=network-online.target docker.service labagent-auth.service" in WEB_UNIT
    assert "Requires=labagent-auth.service" not in WEB_UNIT


def test_installer_only_installs_reloads_and_enables():
    assert '[[ "$EUID" -ne 0 ]]' in INSTALLER
    assert 'SYSTEMD_DIR="/etc/systemd/system"' in INSTALLER
    assert 'install -m 0644 "$UNIT_SOURCE_DIR/labagent-auth.service"' in INSTALLER
    assert 'install -m 0644 "$UNIT_SOURCE_DIR/labagent-web.service"' in INSTALLER
    assert 'require_executable "$CONDA_EXE"' in INSTALLER
    assert "systemctl daemon-reload" in INSTALLER
    assert "systemctl enable labagent-auth.service labagent-web.service" in INSTALLER
    assert not re.search(r"systemctl\s+(start|stop|restart)\b", INSTALLER)
    assert "pkill" not in INSTALLER
    assert not re.search(r"\b(reboot|shutdown)\b", INSTALLER)
    assert 'cat "$RUNTIME_ENV"' not in INSTALLER


def test_deployment_restart_guard_precedes_legacy_process_actions():
    restart = DEPLOYMENT.index("if ($RestartWeb)")
    guard = DEPLOYMENT.index("systemctl show labagent-web.service", restart)
    fail_safe = DEPLOYMENT.index("Legacy -RestartWeb is disabled", guard)
    preflight = DEPLOYMENT.index("Conda environment or Streamlit is unavailable", fail_safe)
    pkill = DEPLOYMENT.index("pkill -f", preflight)
    nohup = DEPLOYMENT.index("nohup env CONDA_EXE=", pkill)

    assert restart < guard < fail_safe < preflight < pkill < nohup
    assert "/etc/systemd/system/labagent-web.service" in DEPLOYMENT[restart:guard]
    assert "WEB_SYSTEMD_MANAGED=1" in DEPLOYMENT[restart:preflight]
    assert '"not-found"' in DEPLOYMENT[guard:preflight]
    assert "Deploy without -RestartWeb" in DEPLOYMENT[guard:preflight]
    assert "systemctl restart" not in DEPLOYMENT[restart:nohup]


def test_legacy_restart_remains_available_when_unit_is_not_found():
    restart = DEPLOYMENT.index("if ($RestartWeb)")
    guard = DEPLOYMENT.index("systemctl show labagent-web.service", restart)
    pkill = DEPLOYMENT.index("pkill -f", guard)
    block = DEPLOYMENT[guard:pkill]

    assert '[ "$WEB_UNIT_LOAD_STATE" != "not-found" ]' in block
    assert "exit 1" in block
    assert "fi" in block


def test_no_old_runtime_or_backup_paths_in_systemd_assets():
    combined = AUTH_UNIT + WEB_UNIT + INSTALLER
    assert "/data/zmr/labagent_runtime" not in combined
    assert "/data/zmr/labagent_backups" not in combined

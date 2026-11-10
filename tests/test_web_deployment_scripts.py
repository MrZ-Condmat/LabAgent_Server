"""Deployment script checks that do not contact the server or start Streamlit."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _bash_executable() -> str | None:
    if os.name == "nt":
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


@pytest.mark.parametrize("mode,expected", [
    ("explicit", "/explicit-conda"),
    ("miniforge", "/miniforge3/bin/conda"),
    ("missing", "conda executable not found"),
])
def test_web_runner_conda_resolution_without_interactive_shell(mode, expected):
    bash = _bash_executable()
    if bash is None:
        pytest.skip("Bash is unavailable")
    shell = r'''
set -eu
root="$PWD"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
mkdir -p "$test_dir/project" "$test_dir/home/miniforge3/bin"
cat > "$test_dir/explicit-conda" <<'FAKE_CONDA'
#!/bin/sh
case "$*" in
  *scripts/resolve_web_runtime.py*)
    printf '0.0.0.0\t8501\n'
    exit 0
    ;;
esac
printf 'used=%s\nargs=%s\n' "$0" "$*"
FAKE_CONDA
chmod +x "$test_dir/explicit-conda"
cp "$test_dir/explicit-conda" "$test_dir/home/miniforge3/bin/conda"
if [ "$TEST_MODE" = explicit ]; then
    env HOME="$test_dir/home" PATH=/usr/bin:/bin CONDA_EXE="$test_dir/explicit-conda" \
        LABAGENT_PROJECT_ROOT="$test_dir/project" bash "$root/scripts/run_web_app.sh"
elif [ "$TEST_MODE" = miniforge ]; then
    env -u CONDA_EXE HOME="$test_dir/home" PATH=/usr/bin:/bin \
        LABAGENT_PROJECT_ROOT="$test_dir/project" bash "$root/scripts/run_web_app.sh"
else
    rm "$test_dir/home/miniforge3/bin/conda"
    env -u CONDA_EXE HOME="$test_dir/home" PATH=/usr/bin:/bin \
        LABAGENT_PROJECT_ROOT="$test_dir/project" bash "$root/scripts/run_web_app.sh"
fi
'''
    result = subprocess.run(
        [bash, "-c", shell],
        cwd=ROOT,
        env={**os.environ, "TEST_MODE": mode},
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if mode == "missing":
        assert result.returncode != 0
        assert expected in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout
        assert "args=run --no-capture-output -n labagent_server python -m streamlit run" in result.stdout


@pytest.mark.parametrize("mode", ["default", "explicit", "miniforge"])
def test_daily_runner_passes_persistent_runtime_file_to_cron_child(tmp_path, mode):
    bash = _bash_executable()
    if bash is None:
        pytest.skip("Bash is unavailable")

    def shell_path(path):
        value = path.resolve().as_posix()
        return f"/{value[0].lower()}{value[2:]}" if os.name == "nt" else value

    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    conda = home / "miniforge3/bin/conda" if mode == "miniforge" else tmp_path / "conda"
    conda.parent.mkdir(parents=True, exist_ok=True)
    conda.write_text(
        '#!/bin/sh\nprintf "runtime=%s\\nargs=%s\\n" "$LABAGENT_ENV_FILE" "$*"\n',
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "HOME": shell_path(home),
        "PATH": "/usr/bin:/bin",
        "LABAGENT_PROJECT_ROOT": shell_path(project),
        "TEST_CONDA": shell_path(conda),
        "TEST_RUNNER": shell_path(ROOT / "scripts/run_daily_report.sh"),
    }
    environment.pop("LABAGENT_CONDA_ENV", None)
    environment.pop("LABAGENT_ENV_FILE", None)
    environment.pop("CONDA_EXE", None)
    if mode != "miniforge":
        environment["CONDA_EXE"] = shell_path(conda)
    expected = "/data/zmr/projects/labagent_runtime/labagent.env"
    if mode == "explicit":
        expected = shell_path(tmp_path / "override.env")
        environment["LABAGENT_ENV_FILE"] = expected
    result = subprocess.run(
        [bash, "-c", 'chmod +x "$TEST_CONDA"; exec bash "$TEST_RUNNER"'],
        cwd=ROOT, env=environment, text=True, capture_output=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    log = (project / "logs/daily_report.log").read_text(encoding="utf-8")
    assert f"runtime={expected}" in log
    assert "args=run -n labagent_server python scripts/generate_daily_report.py --skip-weekends" in log


def test_web_runner_strips_crlf_from_env_host_and_port():
    bash = _bash_executable()
    if bash is None:
        pytest.skip("Bash is unavailable")
    shell = r'''
set -eu
root="$PWD"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
mkdir -p "$test_dir/project"
printf 'STREAMLIT_HOST=0.0.0.0\r\nSTREAMLIT_PORT=8501\r\n' > "$test_dir/project/.env"
cat > "$test_dir/conda" <<'FAKE_CONDA'
#!/bin/sh
case "$*" in
  *scripts/resolve_web_runtime.py*)
    exec "$TEST_PYTHON" "$TEST_RESOLVER"
    ;;
esac
printf 'args=%s\n' "$*"
FAKE_CONDA
chmod +x "$test_dir/conda"
env -u STREAMLIT_HOST -u STREAMLIT_PORT HOME="$test_dir" PATH=/usr/bin:/bin \
    CONDA_EXE="$test_dir/conda" LABAGENT_PROJECT_ROOT="$test_dir/project" \
    bash "$root/scripts/run_web_app.sh"
'''
    result = subprocess.run(
        [bash, "-c", shell],
        cwd=ROOT,
        env={
            **os.environ,
            "TEST_PYTHON": sys.executable,
            "TEST_RESOLVER": str(ROOT / "scripts" / "resolve_web_runtime.py"),
        },
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--server.address 0.0.0.0 --server.port 8501" in result.stdout


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("external", "--server.address 127.0.0.8 --server.port 8601"),
        ("process", "--server.address 127.0.0.9 --server.port 8602"),
        ("missing", "LABAGENT_ENV_FILE"),
    ],
)
def test_web_runner_uses_shared_runtime_resolution(mode, expected):
    bash = _bash_executable()
    if bash is None:
        pytest.skip("Bash is unavailable")
    shell = r'''
set -eu
root="$PWD"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
mkdir -p "$test_dir/project"
printf 'STREAMLIT_HOST=127.0.0.8\r\nSTREAMLIT_PORT=8601\r\n' > "$test_dir/labagent.env"
cat > "$test_dir/conda" <<'FAKE_CONDA'
#!/bin/sh
case "$*" in
  *scripts/resolve_web_runtime.py*) exec "$TEST_PYTHON" "$TEST_RESOLVER" ;;
esac
printf 'args=%s\n' "$*"
FAKE_CONDA
chmod +x "$test_dir/conda"
if [ "$TEST_MODE" = external ]; then
    env -u STREAMLIT_HOST -u STREAMLIT_PORT HOME="$test_dir" PATH=/usr/bin:/bin \
        CONDA_EXE="$test_dir/conda" LABAGENT_PROJECT_ROOT="$test_dir/project" \
        LABAGENT_ENV_FILE="$test_dir/labagent.env" bash "$root/scripts/run_web_app.sh"
elif [ "$TEST_MODE" = process ]; then
    env HOME="$test_dir" PATH=/usr/bin:/bin CONDA_EXE="$test_dir/conda" \
        LABAGENT_PROJECT_ROOT="$test_dir/project" LABAGENT_ENV_FILE="$test_dir/labagent.env" \
        STREAMLIT_HOST=127.0.0.9 STREAMLIT_PORT=8602 bash "$root/scripts/run_web_app.sh"
else
    env -u STREAMLIT_HOST -u STREAMLIT_PORT HOME="$test_dir" PATH=/usr/bin:/bin \
        CONDA_EXE="$test_dir/conda" LABAGENT_PROJECT_ROOT="$test_dir/project" \
        LABAGENT_ENV_FILE="$test_dir/missing.env" bash "$root/scripts/run_web_app.sh"
fi
'''
    result = subprocess.run(
        [bash, "-c", shell],
        cwd=ROOT,
        env={
            **os.environ,
            "TEST_MODE": mode,
            "TEST_PYTHON": sys.executable,
            "TEST_RESOLVER": str(ROOT / "scripts" / "resolve_web_runtime.py"),
        },
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if mode == "missing":
        assert result.returncode != 0
        assert expected in result.stderr
        assert "python -m streamlit run" not in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout


def test_release_deployment_uses_committed_git_archive_and_ignores_untracked():
    source = (ROOT / "deploy_to_server.ps1").read_text(encoding="utf-8-sig")
    assert "git status --porcelain=v1 --untracked-files=no" in source
    assert 'Invoke-Checked "git" @("archive", "--format=tar.gz"' in source
    assert "scripts/rescore_saved_reports.py" not in source
    assert "tar -xzf" not in source
    assert 'throw "Commit tracked changes before staging a release.' in source


def test_release_deployment_stages_without_activation_or_process_changes():
    source = (ROOT / "deploy_to_server.ps1").read_text(encoding="utf-8-sig")
    assert "stage_beta_release.sh" in source
    assert "release_archive.py" in source
    assert "LABAGENT_DEPLOY_ROOT=" in source
    assert "Staged release ID:" in source
    assert "Running services were not modified or restarted." in source
    assert "pkill" not in source
    assert "nohup" not in source
    assert 'Invoke-Checked "ssh"' in source
    assert "sudo -S" not in source
    assert "echo password" not in source.lower()
    assert "systemctl restart" in source  # Printed operator instructions only.
    assert 'Write-Host "  sudo systemctl restart' in source


def test_legacy_mutating_options_fail_before_network_commands():
    source = (ROOT / "deploy_to_server.ps1").read_text(encoding="utf-8-sig")
    push_location = source.index("Push-Location")
    network = source.index('Assert-CommandExists scp', push_location)
    for option in ("RestartWeb", "InstallDeps", "RunSmokeTest", "AllowDirty"):
        guard = source.index(f"if (${option})")
        assert guard < push_location < network
        assert "throw" in source[guard:source.index("}", guard)]


def test_git_head_archive_contains_exactly_committed_files(tmp_path):
    archive = tmp_path / "head.tar"
    result = subprocess.run(
        ["git", "archive", "--format=tar", f"--output={archive}", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    listing = subprocess.run(
        ["tar", "-tf", str(archive)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert listing.returncode == 0, listing.stderr
    tracked = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=ROOT,
        text=True, capture_output=True, check=False,
    )
    assert tracked.returncode == 0, tracked.stderr
    # rescore is now committed; archive membership must follow HEAD rather than
    # an outdated assumption about one particular file being untracked.
    archived_files = {name for name in listing.stdout.splitlines() if not name.endswith("/")}
    assert archived_files == set(tracked.stdout.splitlines())
    assert ".env" not in listing.stdout.splitlines()

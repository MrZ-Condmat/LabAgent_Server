"""Deployment script checks that do not contact the server or start Streamlit."""

import os
from pathlib import Path
import shutil
import subprocess

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


def test_deployment_restart_passes_conda_and_gates_revision_on_health():
    source = (ROOT / "deploy_to_server.ps1").read_text(encoding="utf-8-sig")
    restart = source.index("if ($RestartWeb)")
    preflight = source.index("Conda environment or Streamlit is unavailable", restart)
    stop_old = source.index("pkill -f", restart)
    start_new = source.index("nohup env CONDA_EXE=", restart)
    health = source.index("/_stcore/health", restart)
    failure = source.index("Web app failed to start", restart)
    revision = source.index("logs/deployed_revision.txt.tmp", restart)

    assert restart < preflight < stop_old < start_new < health < failure < revision
    assert "LABAGENT_CONDA_ENV=" in source[start_new:health]
    assert "tail -n 50 logs/web_app.log" in source[failure:revision]
    assert "exit 1" in source[failure:revision]

"""Offline release staging, activation, rollback, and verification tests."""

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from scripts.release_archive import ReleaseArchiveError, load_manifest, safe_extract


ROOT = Path(__file__).resolve().parents[1]
GIT_BASH = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"


def _bash_path(path: Path) -> str:
    result = subprocess.run(
        [str(GIT_BASH), "-lc", 'cygpath -u "$1"', "cygpath", str(path)],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _make_release_archive(
    archive: Path,
    source: Path,
    *,
    omit: str | None = None,
) -> None:
    files = {
        "scripts/release_archive.py": (ROOT / "scripts/release_archive.py").read_bytes(),
        "scripts/validate_runtime_config.py": b"raise SystemExit(0)\n",
        "scripts/wait_for_database.py": b"raise SystemExit(0)\n",
        "scripts/run_web_app.sh": b"#!/usr/bin/env bash\nexit 0\n",
        "lab_agent/api/app.py": b"app = object()\n",
        "lab_agent/__init__.py": b"",
    }
    source.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        if relative == omit:
            continue
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    with tarfile.open(archive, "w:gz") as output:
        for path in sorted(source.rglob("*")):
            output.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)


@pytest.fixture
def release_env(tmp_path):
    if not GIT_BASH.is_file():
        pytest.skip("Git Bash is unavailable")

    deploy = tmp_path / "deploy"
    shared = tmp_path / "shared"
    runtime = tmp_path / "labagent.env"
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    runtime.write_text("TEST_ONLY=true\n", encoding="utf-8")
    for name in ("uvicorn", "conda"):
        _write_executable(fakebin / name, "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fakebin / "flock", "#!/usr/bin/env bash\nexit 0\n")

    junction_script = tmp_path / "junction.ps1"
    junction_script.write_text(
        "param([string]$Target, [string]$Link)\n"
        "New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n",
        encoding="utf-8-sig",
    )
    fake_ln = fakebin / "ln"
    _write_executable(
        fake_ln,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" != "-s" || $# -ne 3 ]]; then exit 2; fi
target="$2"
link="$3"
if [[ "$target" != /* ]]; then
  target="$(cd "$(dirname "$link")" && pwd)/$target"
fi
target_win="$(cygpath -aw "$target")"
link_win="$(cygpath -aw "$link")"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$JUNCTION_SCRIPT" "$target_win" "$link_win"
""",
    )

    systemctl = fakebin / "systemctl"
    _write_executable(
        systemctl,
        """#!/usr/bin/env bash
set -euo pipefail
service="${3:-}"
if [[ "$service" == "${FAIL_SERVICE:-}" ]]; then exit 1; fi
exit 0
""",
    )
    curl = fakebin / "curl"
    _write_executable(
        curl,
        """#!/usr/bin/env bash
set -euo pipefail
url="${@: -1}"
if [[ -n "${FAIL_URL_PART:-}" && "$url" == *"$FAIL_URL_PART"* ]]; then exit 1; fi
printf 'ok\n'
""",
    )

    environment = {
        **os.environ,
        "LABAGENT_DEPLOY_ROOT": _bash_path(deploy),
        "LABAGENT_SHARED_ROOT": _bash_path(shared),
        "LABAGENT_ENV_FILE": _bash_path(runtime),
        "LABAGENT_PYTHON_BIN": _bash_path(Path(sys.executable)),
        "LABAGENT_UVICORN_BIN": _bash_path(fakebin / "uvicorn"),
        "CONDA_EXE": _bash_path(fakebin / "conda"),
        "RELEASE_ARCHIVE_HELPER": _bash_path(ROOT / "scripts/release_archive.py"),
        "SYSTEMCTL_BIN": _bash_path(systemctl),
        "CURL_BIN": _bash_path(curl),
        "JUNCTION_SCRIPT": _bash_path(junction_script),
        "TEST_FAKEBIN": _bash_path(fakebin),
    }

    def run(script_name: str, *args: str, changes: dict[str, str] | None = None):
        env = {**environment, **(changes or {})}
        script = _bash_path(ROOT / "scripts" / script_name)
        converted = [
            _bash_path(Path(arg)) if Path(arg).is_absolute() and Path(arg).exists() else arg
            for arg in args
        ]
        return subprocess.run(
            [
                str(GIT_BASH),
                "-c",
                'export PATH="$TEST_FAKEBIN:$PATH"; exec bash "$@"',
                "release-test",
                script,
                *converted,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def archive(release_id: str, omit: str | None = None) -> Path:
        path = tmp_path / f"{release_id}.tar.gz"
        source = tmp_path / f"source-{release_id}"
        _make_release_archive(path, source, omit=omit)
        return path

    def junction(target: Path, link: Path) -> None:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(junction_script),
                str(target),
                str(link),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    return {
        "tmp": tmp_path,
        "deploy": deploy,
        "shared": shared,
        "run": run,
        "archive": archive,
        "junction": junction,
        "environment": environment,
    }


RELEASE_A = "20260929_103512_aaaaaaaa"
SHA_A = "a" * 40
RELEASE_B = "20260929_104512_bbbbbbbb"
SHA_B = "b" * 40


def _stage(env, release_id=RELEASE_A, sha=SHA_A, omit=None):
    archive = env["archive"](release_id, omit=omit)
    return env["run"]("stage_beta_release.sh", str(archive), release_id, sha)


def _activate(env, release_id):
    return env["run"]("activate_beta_release.sh", release_id)


def test_valid_archive_stages_manifest_and_shared_links(release_env):
    result = _stage(release_env)
    final = release_env["deploy"] / "releases" / RELEASE_A

    assert result.returncode == 0, result.stderr
    assert final.is_dir()
    assert (final / "reports").resolve() == (release_env["shared"] / "reports").resolve()
    assert (final / "logs").resolve() == (release_env["shared"] / "logs").resolve()
    manifest = load_manifest(final / "release.json", RELEASE_A)
    assert manifest["full_git_sha"] == SHA_A
    assert set(manifest) == {
        "release_id",
        "full_git_sha",
        "short_git_sha",
        "created_at_utc",
        "deployment_format_version",
        "archive_sha256",
    }
    manifest_text = (final / "release.json").read_text(encoding="utf-8")
    for forbidden in ("DATABASE_URL", "SMTP_PASSWORD", "API_KEY", "AUTH_SECRET"):
        assert forbidden not in manifest_text
    assert not (release_env["deploy"] / "current").exists()


def test_existing_release_is_never_overwritten(release_env):
    first = _stage(release_env)
    manifest = release_env["deploy"] / "releases" / RELEASE_A / "release.json"
    before = manifest.read_bytes()
    second = _stage(release_env)

    assert first.returncode == 0
    assert second.returncode != 0
    assert manifest.read_bytes() == before
    assert "will not be overwritten" in second.stderr


def test_final_promotion_is_serialized_and_no_clobber_by_convention():
    source = (ROOT / "scripts/stage_beta_release.sh").read_text(encoding="utf-8")
    lock = source.index('flock 9')
    recheck = source.index('[[ -e "$FINAL_DIR"', lock)
    promote = source.index('mv -T -- "$STAGING_DIR" "$FINAL_DIR"', recheck)
    assert lock < recheck < promote


def test_staging_failure_cleans_temporary_directory(release_env):
    result = _stage(release_env, omit="lab_agent/api/app.py")
    releases = release_env["deploy"] / "releases"

    assert result.returncode != 0
    assert not (releases / RELEASE_A).exists()
    assert not (releases / f".staging-{RELEASE_A}").exists()


def test_invalid_release_id_is_rejected_without_layout_changes(release_env):
    archive = release_env["archive"](RELEASE_A)
    result = release_env["run"](
        "stage_beta_release.sh", str(archive), "../escape", SHA_A
    )

    assert result.returncode != 0
    assert not release_env["deploy"].exists()


def test_stage_does_not_modify_existing_current_marker(release_env):
    release_env["deploy"].mkdir(parents=True)
    marker = release_env["deploy"] / "current"
    marker.write_text("unchanged", encoding="utf-8")

    result = _stage(release_env)

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize("member_name", ["../escape", "/absolute/path"])
def test_archive_path_traversal_is_rejected(tmp_path, member_name):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo(member_name)
        payload = b"unsafe"
        member.size = len(payload)
        output.addfile(member, io.BytesIO(payload))

    with pytest.raises(ReleaseArchiveError, match="unsafe path"):
        safe_extract(archive, tmp_path / "output")


def test_archive_symlink_entry_is_rejected(tmp_path):
    archive = tmp_path / "unsafe-link.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("escape-link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../outside"
        output.addfile(member)

    with pytest.raises(ReleaseArchiveError, match="regular files"):
        safe_extract(archive, tmp_path / "output")


def test_activation_updates_current_previous_and_revision(release_env):
    assert _stage(release_env, RELEASE_A, SHA_A).returncode == 0
    assert _stage(release_env, RELEASE_B, SHA_B).returncode == 0
    first = _activate(release_env, RELEASE_A)
    second = _activate(release_env, RELEASE_B)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert (release_env["deploy"] / "current").resolve().name == RELEASE_B
    assert (release_env["deploy"] / "previous").resolve().name == RELEASE_A
    revision = release_env["shared"] / "logs" / "deployed_revision.txt"
    assert revision.read_text(encoding="utf-8").strip() == SHA_B


@pytest.mark.parametrize("failure", ["missing", "malformed"])
def test_activation_refuses_invalid_manifest(release_env, failure):
    assert _stage(release_env).returncode == 0
    manifest = release_env["deploy"] / "releases" / RELEASE_A / "release.json"
    if failure == "missing":
        manifest.unlink()
    else:
        manifest.write_text("{not-json", encoding="utf-8")

    result = _activate(release_env, RELEASE_A)

    assert result.returncode != 0
    assert not (release_env["deploy"] / "current").exists()


def test_activation_refuses_invalid_shared_symlink(release_env):
    assert _stage(release_env).returncode == 0
    reports = release_env["deploy"] / "releases" / RELEASE_A / "reports"
    os.rmdir(reports)
    reports.mkdir()

    result = _activate(release_env, RELEASE_A)

    assert result.returncode != 0
    assert not (release_env["deploy"] / "current").exists()


def test_activation_rejects_invalid_release_id_without_changes(release_env):
    result = _activate(release_env, "../invalid")

    assert result.returncode != 0
    assert not release_env["deploy"].exists()


def test_rollback_swaps_current_and_previous_without_database_action(release_env):
    assert _stage(release_env, RELEASE_A, SHA_A).returncode == 0
    assert _stage(release_env, RELEASE_B, SHA_B).returncode == 0
    assert _activate(release_env, RELEASE_A).returncode == 0
    assert _activate(release_env, RELEASE_B).returncode == 0

    result = release_env["run"]("rollback_beta_release.sh")

    assert result.returncode == 0, result.stderr
    assert (release_env["deploy"] / "current").resolve().name == RELEASE_A
    assert (release_env["deploy"] / "previous").resolve().name == RELEASE_B
    revision = release_env["shared"] / "logs" / "deployed_revision.txt"
    assert revision.read_text(encoding="utf-8").strip() == SHA_A
    rollback_source = (ROOT / "scripts/rollback_beta_release.sh").read_text(encoding="utf-8")
    assert "wait_for_database" not in rollback_source
    assert "alembic" not in rollback_source.lower()
    assert "systemctl" not in rollback_source


def test_rollback_without_previous_fails_cleanly(release_env):
    assert _stage(release_env).returncode == 0
    assert _activate(release_env, RELEASE_A).returncode == 0

    result = release_env["run"]("rollback_beta_release.sh")

    assert result.returncode != 0
    assert (release_env["deploy"] / "current").resolve().name == RELEASE_A


def test_rollback_refuses_previous_outside_release_root(release_env):
    assert _stage(release_env).returncode == 0
    assert _activate(release_env, RELEASE_A).returncode == 0
    outside = release_env["tmp"] / "outside-release"
    outside.mkdir()
    release_env["junction"](outside, release_env["deploy"] / "previous")

    result = release_env["run"]("rollback_beta_release.sh")

    assert result.returncode != 0
    assert (release_env["deploy"] / "current").resolve().name == RELEASE_A


def _prepare_verified_layout(env):
    assert _stage(env, RELEASE_A, SHA_A).returncode == 0
    assert _activate(env, RELEASE_A).returncode == 0
    compat = env["tmp"] / "labAgent_Server"
    env["junction"]((env["deploy"] / "current").resolve(), compat)
    return compat


def test_verify_succeeds_when_services_and_http_are_healthy(release_env):
    compat = _prepare_verified_layout(release_env)

    result = release_env["run"](
        "verify_beta_release.sh",
        changes={"LABAGENT_COMPAT_PATH": _bash_path(compat)},
    )

    assert result.returncode == 0, result.stderr
    assert RELEASE_A in result.stdout


@pytest.mark.parametrize(
    "changes",
    [
        {"FAIL_SERVICE": "labagent-auth.service"},
        {"FAIL_SERVICE": "labagent-web.service"},
        {"FAIL_URL_PART": "8000/healthz"},
        {"FAIL_URL_PART": "8501/_stcore/health"},
    ],
)
def test_verify_fails_for_inactive_service_or_http_failure(release_env, changes):
    compat = _prepare_verified_layout(release_env)
    changes = {**changes, "LABAGENT_COMPAT_PATH": _bash_path(compat)}

    result = release_env["run"]("verify_beta_release.sh", changes=changes)

    assert result.returncode != 0


def test_verify_refuses_current_outside_release_root(release_env):
    outside = release_env["tmp"] / "outside-current"
    outside.mkdir()
    release_env["deploy"].mkdir()
    release_env["junction"](outside, release_env["deploy"] / "current")

    result = release_env["run"]("verify_beta_release.sh")

    assert result.returncode != 0


def test_release_scripts_never_restart_services_or_delete_releases():
    names = (
        "stage_beta_release.sh",
        "activate_beta_release.sh",
        "rollback_beta_release.sh",
    )
    combined = "\n".join((ROOT / "scripts" / name).read_text(encoding="utf-8") for name in names)
    assert "systemctl" not in combined
    assert "sudo" not in combined
    assert "pkill" not in combined
    assert "nohup" not in combined
    assert "alembic" not in combined.lower()
    assert "docker" not in combined.lower()
    assert "rm -rf -- \"$RELEASES_DIR\"" not in combined

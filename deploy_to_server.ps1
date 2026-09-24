param(
    [string]$ServerHost = "166.111.26.183",
    [string]$ServerUser = "zmr",
    [int]$ServerPort = 22,
    [string]$RemoteRoot = "/data/zmr/projects/labAgent_Server",
    [string]$CondaExe = "/home/zmr/miniforge3/bin/conda",
    [string]$CondaEnv = "labagent_server",
    [switch]$RestartWeb,
    [switch]$InstallDeps,
    [switch]$RunSmokeTest,
    [switch]$AllowDirty,
    [switch]$KeepArchive
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-CommandExists {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function ConvertTo-BashSingleQuoted {
    param([string]$Value)
    return "'" + ($Value -replace "'", "'\''") + "'"
}

function Invoke-Checked {
    param(
        [string]$Command,
        [string[]]$Arguments
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command $($Arguments -join ' ')"
    }
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ArchiveName = "labAgent_Server_deploy_$Timestamp.tar.gz"
$ArchivePath = Join-Path $env:TEMP $ArchiveName
$RemoteArchive = "/tmp/$ArchiveName"
$RemoteScript = "/tmp/labAgent_Server_deploy_$Timestamp.sh"
$LocalRemoteScript = Join-Path $env:TEMP "labAgent_Server_deploy_$Timestamp.sh"

Push-Location $ProjectRoot
try {
    Assert-CommandExists git
    Assert-CommandExists tar
    Assert-CommandExists scp
    Assert-CommandExists ssh

    $Dirty = git status --porcelain=v1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not verify Git working tree status."
    }
    if ($Dirty -and -not $AllowDirty) {
        Write-Host "Working tree has uncommitted changes:" -ForegroundColor Yellow
        $Dirty | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
        throw "Commit changes first, or rerun with -AllowDirty if you intentionally want to deploy uncommitted files."
    }

    $Commit = git rev-parse --verify HEAD
    if ($LASTEXITCODE -ne 0 -or $Commit -notmatch '^[0-9a-f]{40,64}$') {
        throw "Could not determine the Git commit to deploy."
    }
    $Revision = if ($Dirty) { "$Commit-dirty" } else { $Commit }

    if (Test-Path -LiteralPath $ArchivePath) {
        Remove-Item -LiteralPath $ArchivePath -Force
    }

    Write-Host "Creating deployment archive..." -ForegroundColor Cyan
    $TarArgs = @(
        "--exclude=.git",
        "--exclude=.env",
        "--exclude=reports",
        "--exclude=logs",
        "--exclude=__pycache__",
        "--exclude=*/__pycache__",
        "--exclude=*.pyc",
        "--exclude=.venv",
        "--exclude=labagent",
        "--exclude=labAgent_Server_deploy*.tar.gz",
        "-czf",
        $ArchivePath,
        "."
    )
    Invoke-Checked "tar" $TarArgs

    $RemoteRootQ = ConvertTo-BashSingleQuoted $RemoteRoot
    $RemoteArchiveQ = ConvertTo-BashSingleQuoted $RemoteArchive
    $RemoteScriptQ = ConvertTo-BashSingleQuoted $RemoteScript
    $RevisionQ = ConvertTo-BashSingleQuoted $Revision
    $CondaExeQ = ConvertTo-BashSingleQuoted $CondaExe
    $CondaEnvQ = ConvertTo-BashSingleQuoted $CondaEnv

    $RemoteLines = @(
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "REMOTE_ROOT=$RemoteRootQ",
        "ARCHIVE=$RemoteArchiveQ",
        "REMOTE_SCRIPT=$RemoteScriptQ",
        "CONDA_EXE=$CondaExeQ",
        "CONDA_ENV=$CondaEnvQ",
        "trap 'rm -f `"`$ARCHIVE`" `"`$REMOTE_SCRIPT`"' EXIT",
        "mkdir -p `"`$REMOTE_ROOT`"",
        "tar -xzf `"`$ARCHIVE`" -C `"`$REMOTE_ROOT`"",
        "cd `"`$REMOTE_ROOT`"",
        "mkdir -p logs",
        "find scripts -maxdepth 1 -type f -name '*.sh' -exec chmod +x {} +",
        "echo `"Deployed archive to `$REMOTE_ROOT`"",
        "echo `"Note: .env, reports, logs, virtualenvs, and Git metadata were not overwritten by the archive.`""
    )

    if ($InstallDeps) {
        $RemoteLines += "CONDA_EXE=`"`$CONDA_EXE`" LABAGENT_CONDA_ENV=`"`$CONDA_ENV`" bash scripts/setup_server.sh"
    }

    if ($RunSmokeTest) {
        $RemoteLines += "`"`$CONDA_EXE`" run -n `"`$CONDA_ENV`" python scripts/generate_daily_report.py --skip-arxiv --skip-journals --skip-highlights"
    }

    if ($RestartWeb) {
        $RemoteLines += "if [ ! -x `"`$CONDA_EXE`" ]; then echo 'Conda executable not found; pass -CondaExe /path/to/conda.' >&2; exit 1; fi"
        $RemoteLines += "if ! `"`$CONDA_EXE`" run -n `"`$CONDA_ENV`" python -c 'import streamlit' >/dev/null 2>&1; then echo 'Conda environment or Streamlit is unavailable; old Web process was left running.' >&2; exit 1; fi"
        $RemoteLines += "if ! command -v curl >/dev/null 2>&1; then echo 'curl is required for the Web health check; old Web process was left running.' >&2; exit 1; fi"
        $RemoteLines += "pkill -f 'streamlit run lab_agent/web/app.py' || true"
        $RemoteLines += "for attempt in {1..10}; do"
        $RemoteLines += "    if ! pgrep -f 'streamlit run lab_agent/web/app.py' >/dev/null; then break; fi"
        $RemoteLines += "    sleep 1"
        $RemoteLines += "done"
        $RemoteLines += "if pgrep -f 'streamlit run lab_agent/web/app.py' >/dev/null; then echo 'Old Web process did not stop; deployed revision was not updated.' >&2; exit 1; fi"
        $RemoteLines += "nohup env CONDA_EXE=`"`$CONDA_EXE`" LABAGENT_CONDA_ENV=`"`$CONDA_ENV`" bash scripts/run_web_app.sh > logs/web_app.log 2>&1 < /dev/null &"
        $RemoteLines += "WEB_PID=`$!"
        $RemoteLines += "WEB_READY=0"
        $RemoteLines += "for attempt in {1..20}; do"
        $RemoteLines += "    if ! kill -0 `"`$WEB_PID`" 2>/dev/null; then break; fi"
        $RemoteLines += "    if curl -fsS --max-time 2 http://127.0.0.1:8501/_stcore/health >/dev/null 2>&1; then WEB_READY=1; break; fi"
        $RemoteLines += "    sleep 1"
        $RemoteLines += "done"
        $RemoteLines += "if [ `"`$WEB_READY`" -ne 1 ]; then"
        $RemoteLines += "    echo 'Web app failed to start; deployed revision was not updated.' >&2"
        $RemoteLines += "    echo 'Last 50 lines of logs/web_app.log:' >&2"
        $RemoteLines += "    tail -n 50 logs/web_app.log >&2"
        $RemoteLines += "    exit 1"
        $RemoteLines += "fi"
        $RemoteLines += "echo 'Web app health check passed.'"
    }

    $RemoteLines += "printf '%s\n' $RevisionQ > logs/deployed_revision.txt.tmp"
    $RemoteLines += "mv logs/deployed_revision.txt.tmp logs/deployed_revision.txt"
    $RemoteLines += "echo `"Recorded deployment revision: $Revision`""

    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($LocalRemoteScript, ($RemoteLines -join "`n") + "`n", $Utf8NoBom)

    $Target = "${ServerUser}@${ServerHost}"
    Write-Host "Uploading archive and remote deploy script to $Target..." -ForegroundColor Cyan
    Invoke-Checked "scp" @("-P", "$ServerPort", $ArchivePath, "${Target}:$RemoteArchive")
    Invoke-Checked "scp" @("-P", "$ServerPort", $LocalRemoteScript, "${Target}:$RemoteScript")

    Write-Host "Applying deployment on server..." -ForegroundColor Cyan
    Invoke-Checked "ssh" @("-p", "$ServerPort", $Target, "bash $RemoteScript")

    Write-Host "Deployment complete." -ForegroundColor Green
}
finally {
    Pop-Location
    if ((Test-Path -LiteralPath $LocalRemoteScript)) {
        Remove-Item -LiteralPath $LocalRemoteScript -Force
    }
    if (-not $KeepArchive -and (Test-Path -LiteralPath $ArchivePath)) {
        Remove-Item -LiteralPath $ArchivePath -Force
    }
}

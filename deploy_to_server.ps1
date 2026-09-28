param(
    [string]$ServerHost = "166.111.26.183",
    [string]$ServerUser = "zmr",
    [int]$ServerPort = 22,
    [string]$DeployRoot = "/data/zmr/projects/labagent_deploy",
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

if ($RestartWeb) {
    throw "-RestartWeb is deprecated for release deployment. Stage, activate, restart systemd services manually, then verify."
}
if ($InstallDeps) {
    throw "-InstallDeps is not supported by immutable release staging. Manage the shared Conda environment as a separate operator step."
}
if ($RunSmokeTest) {
    throw "-RunSmokeTest is not supported by release staging. Use activate, manual systemd restart, and verify_beta_release.sh."
}
if ($AllowDirty) {
    throw "-AllowDirty is disabled. Long-term Beta releases must come from a committed Git HEAD."
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
$TempFiles = New-Object System.Collections.Generic.List[string]
$ArchivePath = $null

Push-Location $ProjectRoot
try {
    Assert-CommandExists git
    Assert-CommandExists scp
    Assert-CommandExists ssh

    $TrackedDirty = git status --porcelain=v1 --untracked-files=no
    if ($LASTEXITCODE -ne 0) {
        throw "Could not verify Git working tree status."
    }
    if ($TrackedDirty) {
        Write-Host "Tracked files have uncommitted changes:" -ForegroundColor Yellow
        $TrackedDirty | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
        throw "Commit tracked changes before staging a release. Untracked files are ignored."
    }

    $Commit = (git rev-parse --verify HEAD).Trim()
    $ShortCommit = (git rev-parse --short=7 HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $Commit -notmatch '^[0-9a-f]{40,64}$') {
        throw "Could not determine the Git commit to deploy."
    }
    if ($ShortCommit -notmatch '^[0-9a-f]{7,12}$' -or -not $Commit.StartsWith($ShortCommit)) {
        throw "Could not determine a valid short Git revision."
    }

    $ReleaseId = "${Timestamp}_${ShortCommit}"
    $ArchiveName = "labagent_release_${ReleaseId}.tar.gz"
    $ArchivePath = Join-Path $env:TEMP $ArchiveName
    $StageCopyName = "stage_beta_release_${ReleaseId}.sh"
    $HelperCopyName = "release_archive_${ReleaseId}.py"
    $DriverName = "stage_beta_release_${ReleaseId}_remote.sh"
    $LocalStageCopy = Join-Path $env:TEMP $StageCopyName
    $LocalHelperCopy = Join-Path $env:TEMP $HelperCopyName
    $LocalDriver = Join-Path $env:TEMP $DriverName
    foreach ($path in @($ArchivePath, $LocalStageCopy, $LocalHelperCopy, $LocalDriver)) {
        $TempFiles.Add($path)
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }

    Write-Host "Creating immutable release archive from Git HEAD..." -ForegroundColor Cyan
    Invoke-Checked "git" @("archive", "--format=tar.gz", "--output=$ArchivePath", "HEAD")
    Copy-Item -LiteralPath "scripts/stage_beta_release.sh" -Destination $LocalStageCopy
    Copy-Item -LiteralPath "scripts/release_archive.py" -Destination $LocalHelperCopy

    $RemoteArchive = "/tmp/$ArchiveName"
    $RemoteStage = "/tmp/$StageCopyName"
    $RemoteHelper = "/tmp/$HelperCopyName"
    $RemoteDriver = "/tmp/$DriverName"
    $ArchiveQ = ConvertTo-BashSingleQuoted $RemoteArchive
    $StageQ = ConvertTo-BashSingleQuoted $RemoteStage
    $HelperQ = ConvertTo-BashSingleQuoted $RemoteHelper
    $DriverQ = ConvertTo-BashSingleQuoted $RemoteDriver
    $ReleaseIdQ = ConvertTo-BashSingleQuoted $ReleaseId
    $CommitQ = ConvertTo-BashSingleQuoted $Commit
    $DeployRootQ = ConvertTo-BashSingleQuoted $DeployRoot

    $RemoteLines = @(
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "ARCHIVE=$ArchiveQ",
        "STAGE_SCRIPT=$StageQ",
        "ARCHIVE_HELPER=$HelperQ",
        "REMOTE_DRIVER=$DriverQ",
        "RELEASE_ID=$ReleaseIdQ",
        "REVISION=$CommitQ",
        "DEPLOY_ROOT=$DeployRootQ",
        "trap 'rm -f -- `"`$ARCHIVE`" `"`$STAGE_SCRIPT`" `"`$ARCHIVE_HELPER`" `"`$REMOTE_DRIVER`"' EXIT",
        "RELEASE_ARCHIVE_HELPER=`"`$ARCHIVE_HELPER`" LABAGENT_DEPLOY_ROOT=`"`$DEPLOY_ROOT`" bash `"`$STAGE_SCRIPT`" `"`$ARCHIVE`" `"`$RELEASE_ID`" `"`$REVISION`""
    )
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($LocalDriver, ($RemoteLines -join "`n") + "`n", $Utf8NoBom)

    $Target = "${ServerUser}@${ServerHost}"
    Write-Host "Uploading release archive and staging helpers to $Target..." -ForegroundColor Cyan
    $ScpArguments = @("-P", "$ServerPort", $ArchivePath, $LocalStageCopy, $LocalHelperCopy, $LocalDriver, "${Target}:/tmp/")
    Invoke-Checked "scp" $ScpArguments

    Write-Host "Staging immutable release on server..." -ForegroundColor Cyan
    Invoke-Checked "ssh" @("-p", "$ServerPort", $Target, "bash $RemoteDriver")

    $ReleaseDirectory = "$DeployRoot/releases/$ReleaseId"
    Write-Host "Release staged successfully." -ForegroundColor Green
    Write-Host "Staged release ID: $ReleaseId"
    Write-Host "Full revision: $Commit"
    Write-Host "Release directory: $ReleaseDirectory"
    Write-Host "Running services were not modified or restarted."
    Write-Host "Next server steps:"
    Write-Host "  bash $ReleaseDirectory/scripts/activate_beta_release.sh $ReleaseId"
    Write-Host "  sudo systemctl restart labagent-auth.service"
    Write-Host "  sudo systemctl restart labagent-web.service"
    Write-Host "  bash /data/zmr/projects/labAgent_Server/scripts/verify_beta_release.sh"
}
finally {
    Pop-Location
    foreach ($path in $TempFiles) {
        if ($KeepArchive -and $null -ne $ArchivePath -and $path -eq $ArchivePath) {
            continue
        }
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }
}

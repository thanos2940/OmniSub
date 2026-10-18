<#
.SYNOPSIS
  Updates OmbiSub in place (what update.cmd runs): stop the task, git pull --ff-only, install the locked
  Python packages, rebuild the web UI when it changed, start the task again.

.DESCRIPTION
  Refuses to run over uncommitted changes or local commits (git pull --ff-only fails then). The task is
  started again even when a step fails, so a failed update does not leave the server down.
  Data (config, databases, projects, logs) is never touched.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'OmbiSub',
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  [FAIL] $m" -ForegroundColor Red; throw $m }

$Root     = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Backend  = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$VenvPy   = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path (Join-Path $Root '.git'))) { Fail "$Root is not a git checkout; there is nothing to pull." }
if (-not (Test-Path $VenvPy)) { Fail "No .venv found in $Root. Run install.cmd first." }
Set-Location $Root

# The task's own arguments say where the data folder is (lock files live there).
$DataDir = $Backend
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $taskArgs = ($task.Actions | ForEach-Object { $_.Arguments }) -join ' '
    if ($taskArgs -match '--data-dir\s+"?([^"]+?)"?(\s|$|>)') { $DataDir = $Matches[1] }
} else { Warn "No scheduled task '$TaskName': the files are updated, nothing is stopped or started." }

function Stop-Leftovers {
    # Stopping the task ends the supervisor; make sure no server it started is left holding the files.
    foreach ($name in 'ombisub_tray.lock', 'ombisub_server.lock') {
        $lock = Join-Path $DataDir $name
        if (-not (Test-Path $lock)) { continue }
        try {
            $info = Get-Content $lock -Raw | ConvertFrom-Json
            $p = Get-Process -Id $info.pid -ErrorAction SilentlyContinue
            if ($p -and $p.ProcessName -match 'python') { & taskkill /PID $info.pid /T /F | Out-Null; Ok "Stopped $($p.ProcessName) PID $($info.pid)" }
        } catch { }
    }
}

$before = (& git rev-parse HEAD).Trim()
try {
    Step 'Checking the checkout'
    if (& git status --porcelain) { Fail 'There are uncommitted changes; commit or stash them first.' }

    Step "Stopping '$TaskName'"
    if ($task) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue; Start-Sleep -Seconds 3 }
    Stop-Leftovers

    Step 'git pull --ff-only'
    & git pull --ff-only
    if ($LASTEXITCODE -ne 0) { Fail 'git pull --ff-only failed (local commits that are not on origin?).' }
    $after = (& git rev-parse HEAD).Trim()
    if ($before -eq $after) { Ok "Already up to date ($($after.Substring(0, 7)))." }
    else { Ok "Updated $($before.Substring(0, 7)) -> $($after.Substring(0, 7))" }

    Step 'Python packages'
    $req = Join-Path $Backend 'requirements.lock.txt'
    if (-not (Test-Path $req)) { $req = Join-Path $Backend 'requirements.txt' }
    & $VenvPy -m pip install -r $req --quiet
    if ($LASTEXITCODE -ne 0) { Fail 'pip install failed.' }
    Ok "Installed from $(Split-Path $req -Leaf)"

    Step 'Web UI'
    $static = Join-Path $Backend 'static'
    $uiChanged = $before -ne $after -and (& git diff --name-only $before $after -- frontend)
    if ($SkipBuild) { Warn 'Skipping the UI build (-SkipBuild).' }
    elseif (-not $uiChanged -and (Test-Path (Join-Path $static 'index.html'))) { Ok 'frontend/ did not change; the UI build is kept.' }
    elseif (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Warn 'npm was not found: the UI was not rebuilt.' }
    else {
        Push-Location $Frontend
        try {
            & npm ci
            if ($LASTEXITCODE -ne 0) { Fail 'npm ci failed.' }
            & npm run build
            if ($LASTEXITCODE -ne 0) { Fail 'npm run build failed.' }
        } finally { Pop-Location }
        $dist = Join-Path $Frontend 'dist'
        if (-not (Test-Path (Join-Path $dist 'index.html'))) { Fail 'The build produced no frontend\dist\index.html.' }
        $new = Join-Path $Backend 'static.new'; $old = Join-Path $Backend 'static.old'
        foreach ($p in $new, $old) { if (Test-Path $p) { Remove-Item $p -Recurse -Force } }
        Copy-Item $dist $new -Recurse
        if (Test-Path $static) { Rename-Item $static 'static.old' }
        try { Rename-Item $new 'static' }
        catch { if (Test-Path $old) { Rename-Item $old 'static' }; throw }
        if (Test-Path $old) { Remove-Item $old -Recurse -Force }
        Ok "UI rebuilt in $static"
    }
}
finally {
    if ($task) {
        Step "Starting '$TaskName'"
        try { Start-ScheduledTask -TaskName $TaskName; Ok 'Task started.' } catch { Warn "Could not start the task: $_" }
    }
}
Write-Host "`nDone. Check: deploy\windows\status.ps1" -ForegroundColor Cyan

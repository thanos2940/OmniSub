<#
.SYNOPSIS
  Installs or updates OmbiSub as a 24/7 Windows server (Task Scheduler task).

.DESCRIPTION
  Checks prerequisites, creates/updates .venv, installs backend requirements, builds the web UI into
  backend\static (atomic swap) and registers the scheduled task. Safe to re-run: it updates the venv, the
  build and the task. The password you type is passed to Task Scheduler only; it is never written to a file.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1
  powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Port 8080 -DataDir D:\OmbiSubData -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$InstallDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$DataDir = '',
    [int]$Port = 8000,
    [string]$TaskName = 'OmbiSub',
    [string]$User = "$env:USERDOMAIN\$env:USERNAME",
    [switch]$SkipBuild,
    [switch]$AlsoAtLogon
)

$ErrorActionPreference = 'Stop'
function Step($m)  { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok($m)    { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Warn($m)  { Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Fail($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red; throw $m }

$InstallDir = (Resolve-Path $InstallDir).Path
$Backend  = Join-Path $InstallDir 'backend'
$Frontend = Join-Path $InstallDir 'frontend'
$Venv     = Join-Path $InstallDir '.venv'
$VenvPy   = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path (Join-Path $Backend 'run_server.py'))) { Fail "backend\run_server.py not found under $InstallDir (is -InstallDir the repo root?)" }

# ---------------------------------------------------------------- prerequisites
Step 'Checking prerequisites'
function Find-Python {
    foreach ($cand in @(@('python'), @('py', '-3'))) {
        $exe = Get-Command $cand[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        try {
            $extra = @(); if ($cand.Count -gt 1) { $extra = $cand[1..($cand.Count - 1)] }
            $ver = & $exe.Source @extra -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])' 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver) {
                $v = [version]$ver
                if ($v -ge [version]'3.11') { return @{ Exe = $exe.Source; Args = $extra; Version = $ver } }
            }
        } catch { }
    }
    return $null
}
$py = Find-Python
if (-not $py) { Fail 'Python 3.11 or newer was not found on PATH (python / py -3). Install it from python.org ("Add Python to PATH").' }
Ok "Python $($py.Version): $($py.Exe)"
if (Test-Path $VenvPy) { Ok "Existing venv: $Venv" } else { Ok "A venv will be created at $Venv" }

$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($SkipBuild) { Warn 'Skipping the UI build (-SkipBuild).' }
elseif ($npm) { Ok "Node/npm (build only): $((& node --version 2>$null))" }
else { Fail 'Node.js (npm) is needed to build the web UI. Install Node LTS, or pass -SkipBuild if backend\static is already built.' }

foreach ($t in 'ffmpeg', 'ffprobe') {
    $c = Get-Command $t -ErrorAction SilentlyContinue
    if ($c) { Ok "$t : $($c.Source)" }
    else { Warn "$t is not on PATH. Embedded subtitle tracks cannot be read/extracted. Put ffmpeg\bin on the SYSTEM PATH (the task runs outside your shell)." }
}

$localApp = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
$cliHints = @{
    'agy'    = @((Join-Path $localApp 'agy\bin\agy.exe'))
    'claude' = @((Join-Path $HOME '.local\bin\claude.exe'))
}
$foundCli = $false
foreach ($name in 'agy', 'claude') {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    $path = if ($c) { $c.Source } else { $cliHints[$name] | Where-Object { Test-Path $_ } | Select-Object -First 1 }
    if ($path) { Ok "$name CLI: $path"; $foundCli = $true } else { Warn "$name CLI not found." }
}
if (-not $foundCli) { Warn 'No agent CLI (agy / claude) found: the agent runner cannot translate until one is installed.' }
Warn "The CLIs must be signed in as '$User' (the account the task runs as). Sign in once in a normal terminal as that user."

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Warn 'Not running as Administrator: registering a startup task for "run whether logged on or not" usually needs an elevated PowerShell.' }

# ---------------------------------------------------------------- venv + requirements
Step 'Python environment'
if ($PSCmdlet.ShouldProcess($Venv, 'Create/update venv and install backend\requirements.txt')) {
    if (-not (Test-Path $VenvPy)) {
        & $py.Exe @($py.Args) -m venv $Venv
        if ($LASTEXITCODE -ne 0) { Fail 'Creating the venv failed.' }
    }
    & $VenvPy -m pip install --upgrade pip --quiet
    Write-Host '  installing backend\requirements.txt (the first time this downloads several hundred MB) ...'
    & $VenvPy -m pip install -r (Join-Path $Backend 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Fail 'pip install failed.' }
    Ok 'Requirements installed.'
}

# ---------------------------------------------------------------- frontend build
if (-not $SkipBuild) {
    Step 'Building the web UI'
    if ($PSCmdlet.ShouldProcess((Join-Path $Backend 'static'), 'npm ci + npm run build, then swap into backend\static')) {
        Push-Location $Frontend
        try {
            & npm ci
            if ($LASTEXITCODE -ne 0) { Fail 'npm ci failed.' }
            & npm run build
            if ($LASTEXITCODE -ne 0) { Fail 'npm run build failed.' }
        } finally { Pop-Location }
        $dist = Join-Path $Frontend 'dist'
        if (-not (Test-Path (Join-Path $dist 'index.html'))) { Fail 'The build produced no frontend\dist\index.html.' }
        $static = Join-Path $Backend 'static'; $new = Join-Path $Backend 'static.new'; $old = Join-Path $Backend 'static.old'
        foreach ($p in $new, $old) { if (Test-Path $p) { Remove-Item $p -Recurse -Force } }
        Copy-Item $dist $new -Recurse
        if (Test-Path $static) { Rename-Item $static 'static.old' }
        try { Rename-Item $new 'static' }
        catch { if (Test-Path $old) { Rename-Item $old 'static' }; throw }
        if (Test-Path $old) { Remove-Item $old -Recurse -Force }
        Ok "UI installed in $static"
    }
}

# ---------------------------------------------------------------- scheduled task
Step "Registering the scheduled task '$TaskName'"
$argList = "backend\run_server.py --port $Port"
if ($DataDir) { $argList += " --data-dir `"$DataDir`"" }
$action   = New-ScheduledTaskAction -Execute $VenvPy -Argument $argList -WorkingDirectory $InstallDir
$triggers = @(New-ScheduledTaskTrigger -AtStartup)
if ($AlsoAtLogon) { $triggers += New-ScheduledTaskTrigger -AtLogOn -User $User }
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
# no -WakeToRun: not required; keeping the PC awake is a power-plan setting (see the next steps)

if ($PSCmdlet.ShouldProcess($TaskName, "Register task as $User (startup, run whether logged on or not, restart every 1 min x 999)")) {
    $cred = Get-Credential -UserName $User -Message "Windows password for $User (used by Task Scheduler only; not stored by this script)"
    if (-not $cred) { Fail 'No credentials given.' }
    $plain = $cred.GetNetworkCredential().Password
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Warn "Task '$TaskName' exists: stopping and replacing it."
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
            -User $cred.UserName -Password $plain -RunLevel Limited `
            -Description 'OmbiSub subtitle translator + Autopilot (runs backend\run_server.py)' | Out-Null
    } finally { $plain = $null }
    Ok "Task '$TaskName' registered."
    Start-ScheduledTask -TaskName $TaskName
    Ok 'Task started.'
}

# ---------------------------------------------------------------- next steps
$logDir = if ($DataDir) { Join-Path $DataDir 'logs' } else { Join-Path $Backend 'logs' }
Write-Host @"

Next steps
  1. Open http://localhost:$Port (give it ~20 s on first start). Check: deploy\windows\status.ps1
  2. Settings: Sonarr/Radarr URL + API key, target language, Discord webhook, Plex URL + token,
     then switch Autopilot on (grace: 5 minutes).
  3. Sign in to the agent CLIs AS $User : run 'agy' (and/or 'claude') once in a normal terminal as that user.
  4. Queue page -> Agent runner -> Keep watching.
  5. Keep the machine awake while plugged in:  powercfg /change standby-timeout-ac 0
     (and hibernate-timeout-ac 0). Task Scheduler cannot do this for you.
  6. Windows Firewall: allow inbound TCP $Port only if other devices must reach the UI.
Logs: $logDir\ombisub.log   (rotating, 10 MB x 5)
Update later: Stop-ScheduledTask $TaskName ; git pull ; deploy\windows\install.ps1
"@

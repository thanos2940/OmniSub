# ==============================================================================
# OmbiSub Windows Service Installer (using NSSM)
# ==============================================================================
# Run this script in an elevated PowerShell session (Run as Administrator)
# ==============================================================================

param (
    [string]$ServiceName = "OmbiSub",
    [int]$Port = 8000,
    [string]$ServiceUser = "",
    [string]$ServicePassword = ""
)

$ErrorActionPreference = "Stop"

# Ensure Administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must be run as Administrator. Please right-click PowerShell and choose 'Run as Administrator'."
    exit 1
}

$RootDir = (Get-Item "$PSScriptRoot\..").FullName
$BackendDir = Join-Path $RootDir "backend"
$PythonExe = Join-Path $RootDir ".venv\Scripts\python.exe"
$LogsDir = Join-Path $RootDir "logs"
$StdoutLog = Join-Path $LogsDir "service_stdout.log"
$StderrLog = Join-Path $LogsDir "service_stderr.log"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python executable not found at: $PythonExe. Ensure virtualenv exists."
    exit 1
}

if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}

# Locate or Install NSSM
$nssmCmd = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $nssmCmd) {
    Write-Host "[*] NSSM not found in PATH. Attempting automated install via winget..." -ForegroundColor Cyan
    try {
        & winget install NSSM.NSSM --accept-source-agreements --accept-package-agreements --silent
        # Refresh environment PATH for current session
        $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $env:Path = "$machinePath;$userPath"
        $nssmCmd = Get-Command nssm -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "winget installation failed: $_"
    }
}

if (-not $nssmCmd) {
    # Check default winget install locations for nssm
    $possibleNssm = @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\nssm.exe",
        "$env:ProgramFiles\nssm\nssm.exe",
        "$env:ProgramFiles\nssm\win64\nssm.exe",
        "C:\ProgramData\chocolatey\bin\nssm.exe"
    )
    foreach ($cand in $possibleNssm) {
        if (Test-Path $cand) {
            $nssmPath = $cand
            break
        }
    }
} else {
    $nssmPath = $nssmCmd.Source
}

if (-not $nssmPath -or -not (Test-Path $nssmPath)) {
    Write-Error "NSSM executable could not be found. Please install NSSM ('winget install NSSM.NSSM') or place nssm.exe on your PATH and re-run."
    exit 1
}

Write-Host "[+] Using NSSM at: $nssmPath" -ForegroundColor Green

# Check if service already exists; remove if present
$existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "[*] Stopping and removing existing service '$ServiceName'..." -ForegroundColor Yellow
    & $nssmPath stop $ServiceName 2>&1 | Out-Null
    & $nssmPath remove $ServiceName confirm 2>&1 | Out-Null
    Start-Sleep -Seconds 2
}

# Install service with NSSM
Write-Host "[*] Installing service '$ServiceName'..." -ForegroundColor Cyan
$arguments = "-m uvicorn main:app --host 0.0.0.0 --port $Port"

& $nssmPath install $ServiceName "$PythonExe" $arguments
& $nssmPath set $ServiceName AppDirectory "$BackendDir"
& $nssmPath set $ServiceName DisplayName "OmbiSub AI Subtitle Translation Service"
& $nssmPath set $ServiceName Description "Autonomous AI-powered subtitle localization and translation engine for Plex, Sonarr, and Radarr."
& $nssmPath set $ServiceName Start SERVICE_AUTO_START
& $nssmPath set $ServiceName AppStdout "$StdoutLog"
& $nssmPath set $ServiceName AppStderr "$StderrLog"

# Log rotation: offline only (NSSM online rotation causes Win32 sharing violations)
& $nssmPath set $ServiceName AppRotateFiles 1
& $nssmPath set $ServiceName AppRotateOnline 0
& $nssmPath set $ServiceName AppRotateSeconds 86400
& $nssmPath set $ServiceName AppRotateBytes 10485760 # 10MB

# Process management & CPU priority: BELOW_NORMAL prevents CPU starvation on Plex hosts
& $nssmPath set $ServiceName AppRestartDelay 5000
& $nssmPath set $ServiceName AppPriority BELOW_NORMAL_PRIORITY_CLASS

# Graceful shutdown timeouts: allow Uvicorn 1500ms to exit cleanly on SIGINT before kill
& $nssmPath set $ServiceName AppStopMethodConsole 1500
& $nssmPath set $ServiceName AppStopMethodKill 1500

# Configure Service User Account for SMB network share authentication (INT-P0-03)
if ($ServiceUser) {
    Write-Host "[*] Configuring service user account '$ServiceUser' for SMB share access..." -ForegroundColor Cyan
    if ($ServicePassword) {
        & $nssmPath set $ServiceName ObjectName $ServiceUser $ServicePassword
    } else {
        & $nssmPath set $ServiceName ObjectName $ServiceUser ""
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to set NSSM ObjectName for user '$ServiceUser'."
        exit 1
    }
    Write-Host "[+] Service credentials configured successfully." -ForegroundColor Green
} else {
    Write-Host "[!] Note: -ServiceUser not provided. Service will run as LocalSystem." -ForegroundColor Yellow
    Write-Host "    If your media library resides on an authenticated SMB share or mapped drive," -ForegroundColor Yellow
    Write-Host "    re-run: .\install_windows_service.ps1 -ServiceUser '.\User' -ServicePassword 'Pass'" -ForegroundColor Yellow
}

# Start Service
Write-Host "[*] Starting service '$ServiceName'..." -ForegroundColor Cyan
& $nssmPath start $ServiceName

Start-Sleep -Seconds 3
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host "[✓] OmbiSub is now running as a background Windows Service!" -ForegroundColor Green
    Write-Host "    Web Dashboard URL: http://localhost:$Port" -ForegroundColor Cyan
    Write-Host "    Log location:      $StdoutLog" -ForegroundColor Gray
} else {
    Write-Warning "Service was installed but does not appear to be running. Check $StderrLog for details."
}

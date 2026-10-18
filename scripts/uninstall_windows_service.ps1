# ==============================================================================
# OmbiSub Windows Service Uninstaller
# ==============================================================================
# Run in an elevated PowerShell session (Run as Administrator)
# ==============================================================================

param (
    [string]$ServiceName = "OmbiSub"
)

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must be run as Administrator. Please right-click PowerShell and choose 'Run as Administrator'."
    exit 1
}

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "[!] Service '$ServiceName' does not exist." -ForegroundColor Yellow
    exit 0
}

# Find NSSM
$nssmCmd = Get-Command nssm -ErrorAction SilentlyContinue
$nssmPath = if ($nssmCmd) { $nssmCmd.Source } else { "nssm.exe" }

Write-Host "[*] Stopping and removing service '$ServiceName'..." -ForegroundColor Cyan
try {
    & $nssmPath stop $ServiceName 2>&1 | Out-Null
} catch {}

try {
    & $nssmPath remove $ServiceName confirm
    Write-Host "[✓] Service '$ServiceName' removed successfully." -ForegroundColor Green
} catch {
    # Fallback to standard sc.exe if nssm fails
    sc.exe delete $ServiceName
    Write-Host "[✓] Service '$ServiceName' deleted via sc.exe." -ForegroundColor Green
}

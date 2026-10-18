<#
.SYNOPSIS
  Shows the OmbiSub task state, last run result, server process, /api/health and the Autopilot status.
#>
param(
    [string]$TaskName = 'OmbiSub',
    [int]$Port = 8000,
    [string]$DataDir = ''
)

function Show($label, $value) { Write-Host ("{0,-22}: {1}" -f $label, $value) }

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) { Write-Host "Scheduled task '$TaskName' is not registered (run deploy\windows\install.ps1)." -ForegroundColor Yellow }
else {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Show 'Task state' $task.State
    Show 'Last run' $info.LastRunTime
    Show 'Last result' ("{0} (0 = ok, 267009 = running, 267011 = not yet run)" -f $info.LastTaskResult)
    Show 'Next run' $info.NextRunTime
}

$backend = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path 'backend'
if (-not $DataDir) { $DataDir = $backend }
$lock = Join-Path $DataDir 'ombisub_server.lock'
if (Test-Path $lock) {
    try {
        $l = Get-Content $lock -Raw | ConvertFrom-Json
        $p = Get-Process -Id $l.pid -ErrorAction SilentlyContinue
        if ($p) { Show 'Server process' ("PID {0}, port {1}, started {2}, memory {3} MB" -f $l.pid, $l.port, $l.started, [int]($p.WorkingSet64 / 1MB)); $Port = $l.port }
        else { Show 'Server process' "not running (stale lock for PID $($l.pid))" }
    } catch { Show 'Server process' 'lock file unreadable' }
} else { Show 'Server process' "no lock file in $DataDir (not running, or a different -DataDir)" }

function Get-Json($url) {
    try { return @{ Ok = $true; Data = (Invoke-RestMethod -Uri $url -TimeoutSec 5) } }
    catch {
        $code = $null; if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        return @{ Ok = $false; Code = $code; Error = $_.Exception.Message }
    }
}

$h = Get-Json "http://localhost:$Port/api/health"
if ($h.Ok) { Show '/api/health' ("OK  " + ($h.Data | ConvertTo-Json -Compress -Depth 3)) }
else { Show '/api/health' "UNREACHABLE ($($h.Error))" }

$a = Get-Json "http://localhost:$Port/api/autopilot/status"
if ($a.Ok) {
    $s = $a.Data
    Show 'Autopilot enabled' $s.enabled
    Show 'Autopilot running' $s.running
    if ($s.counts) { Show 'Items by state' (($s.counts.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ', ') }
    if ($s.engine) { Show 'Engine' ("runner={0} running={1} ok={2} {3}" -f $s.engine.runner_status, $s.engine.runner_running, $s.engine.ok, $s.engine.message) }
    foreach ($src in 'sonarr', 'radarr') { if ($s.arr.$src) { Show $src ("configured={0} ok={1} {2}" -f $s.arr.$src.configured, $s.arr.$src.ok, $s.arr.$src.error) } }
}
elseif ($a.Code -eq 404) { Show 'Autopilot' 'not available in this build (404)' }
else { Show 'Autopilot' "unavailable ($($a.Error))" }

$log = Join-Path $DataDir 'logs\ombisub.log'
if (Test-Path $log) { Write-Host "`nLast log lines ($log):"; Get-Content $log -Tail 8 }

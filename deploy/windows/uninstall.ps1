<#
.SYNOPSIS
  Removes the Omnisub scheduled task and stops the server. Your data (config, databases, projects, logs),
  .venv and backend\static are kept.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$TaskName = 'Omnisub')

# Installs from before the rename have a task called 'OmbiSub': use it when no other name was given.
if (-not $PSBoundParameters.ContainsKey('TaskName') -and -not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) -and (Get-ScheduledTask -TaskName 'OmbiSub' -ErrorAction SilentlyContinue)) { $TaskName = 'OmbiSub' }

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) { Write-Host "No scheduled task named '$TaskName'. Nothing to do."; return }

# The data folder (where the lock files live) is in the task's arguments when it was installed with -DataDir.
$dataDir = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path 'backend'
$taskArgs = ($task.Actions | ForEach-Object { $_.Arguments }) -join ' '
if ($taskArgs -match '--data-dir\s+"?([^"]+?)"?(\s|$|>)') { $dataDir = $Matches[1] }

if ($PSCmdlet.ShouldProcess($TaskName, 'Stop and unregister scheduled task')) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Start-Sleep -Seconds 2
    # Ending the task ends the supervisor; stop the server it started as well, if it is still around.
    foreach ($name in 'omnisub_tray.lock', 'omnisub_server.lock', 'ombisub_tray.lock', 'ombisub_server.lock') {
        $lock = Join-Path $dataDir $name
        if (-not (Test-Path $lock)) { continue }
        try {
            $info = Get-Content $lock -Raw | ConvertFrom-Json
            $p = Get-Process -Id $info.pid -ErrorAction SilentlyContinue
            if ($p -and $p.ProcessName -match 'python') { & taskkill /PID $info.pid /T /F | Out-Null; Write-Host "Stopped $($p.ProcessName) PID $($info.pid)." }
        } catch { }
    }
    Write-Host "Task '$TaskName' removed. Data, .venv and the UI build were left in place."
}

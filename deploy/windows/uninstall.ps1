<#
.SYNOPSIS
  Removes the OmbiSub scheduled task. Your data (config, databases, projects, logs), .venv and backend\static are kept.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$TaskName = 'OmbiSub')

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) { Write-Host "No scheduled task named '$TaskName'. Nothing to do."; return }
if ($PSCmdlet.ShouldProcess($TaskName, 'Stop and unregister scheduled task')) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Task '$TaskName' removed. Data, .venv and the UI build were left in place."
    Write-Host 'If a server process is still running, close it (or reboot); it stops cleanly on Ctrl+C / task stop.'
}

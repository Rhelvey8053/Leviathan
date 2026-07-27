# Registers a Windows Task Scheduler job for the run-absence heartbeat.
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_heartbeat_scheduler.ps1
#
# Runs independently of Leviathan-DailyRun by design -- the whole point is
# to detect that scheduler failing to fire at all, so this must be its own
# task, checked at a cadence unrelated to the main run's own schedule.

$TaskName   = "Leviathan-Heartbeat"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\heartbeat_check.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $WorkDir

# Check twice daily (2:00 PM and 8:00 PM) -- well after the 7:00 AM main
# run's own schedule, so a normal day's run has long since either landed
# or not, but frequent enough that a stopped scheduler is caught same-day
# rather than at the next scheduled heartbeat 24h later.
$Trigger1 = New-ScheduledTaskTrigger -Daily -At "02:00PM"
$Trigger2 = New-ScheduledTaskTrigger -Daily -At "08:00PM"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action   $Action `
    -Trigger  @($Trigger1, $Trigger2) `
    -Settings $Settings `
    -RunLevel Highest `
    -Force

Write-Host ""
Write-Host "Task '$TaskName' registered. Runs daily at 2:00pm and 8:00pm."
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

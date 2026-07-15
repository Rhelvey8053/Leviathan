# Registers a Windows Task Scheduler job to run the daily resolve-first selector.
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_resolve_first_scheduler.ps1

$TaskName   = "Leviathan-ResolveFirst"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\daily_resolve_first.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent
$LogPath    = "$WorkDir\data\resolve_first_scheduler.log"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action  = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $WorkDir

# Run daily at 8:30am local time -- after the 7:00am main run and the
# 8:07am smart-money scan, so the market snapshot is fresh.
$Trigger = New-ScheduledTaskTrigger -Daily -At "08:30AM"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action   $Action `
    -Trigger  $Trigger `
    -Settings $Settings `
    -RunLevel Highest `
    -Force

Write-Host ""
Write-Host "Task '$TaskName' registered. Runs daily at 8:30am."
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

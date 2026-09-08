# Registers a Windows Task Scheduler job for the daily operations digest.
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_daily_digest_scheduler.ps1
#
# Runs once daily at 10:00am, after every other daily task has had a real
# chance to finish and write its output: ResolveFirst (~8:30am), GateNotifier
# (~8:45am), SubscriberReport (~9:00am), PositionReconciliation (~9:15am),
# SmartMoneyScan (~7:07am). AutomationHealthCheck (3:00pm) runs later and
# keeps its own separate alert-only email -- this digest calls the same
# underlying check functions directly rather than waiting on that task.

$TaskName   = "Leviathan-DailyDigest"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\daily_digest.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $WorkDir

$Trigger = New-ScheduledTaskTrigger -Daily -At "10:00AM"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# S4U (run whether the user is logged on or not) -- explicit here so
# re-running this script later can never silently regress it back to
# Interactive the way Leviathan-SubscriberReport and Leviathan-WeeklyAudit
# both did (found in a 2026-08-17 audit).
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $Action `
    -Trigger   $Trigger `
    -Settings  $Settings `
    -Principal $Principal `
    -Force

Write-Host ""
Write-Host "Task '$TaskName' registered. Runs daily at 10:00am."
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

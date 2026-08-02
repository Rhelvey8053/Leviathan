# Registers a Windows Task Scheduler job to run the daily position reconciliation.
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_position_reconciliation_scheduler.ps1

$TaskName   = "Leviathan-PositionReconciliation"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\position_reconciliation.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action  = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $WorkDir

# Run daily at 9:15am local time -- after DailyRun (6:00am), SmartMoneyScan
# (7:07am), ResolveFirst (8:30am), GateNotifier (8:45am) and
# SubscriberReport (9:00am), so both the paper-signal set and the Kalshi
# position snapshot it reconciles against are current, and it doesn't add
# to the CPU contention of that earlier 6-9am cluster.
$Trigger = New-ScheduledTaskTrigger -Daily -At "09:15AM"

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
Write-Host "Task '$TaskName' registered. Runs daily at 9:15am."
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

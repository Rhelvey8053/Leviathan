# Registers a Windows Task Scheduler job to run the daily position reconciliation.
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_position_reconciliation_scheduler.ps1

$TaskName   = "Leviathan-PositionReconciliation"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\position_reconciliation.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent
$LogPath    = "$WorkDir\logs\position_reconciliation.log"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# cmd.exe wrapper + output redirection (2026-09-16, backlog:
# smtp-send-no-timeout-hang-2026-09's own follow-up) -- this task was one
# of two (with SmartMoneyScan) that hung its full ExecutionTimeLimit
# during the 2026-09-13 systemic SSL/TLS incident with no stdout/stderr
# captured at all -- its hang genuinely lost that day's reconciliation
# output (data/reconciliation/2026-09-13.json was never written), and the
# real hang point was never directly observed, only inferred (see
# reports/code_audits/2026-09-13.md). Same pattern every other Leviathan-*
# task with output capture already uses (GateNotifier, WeeklyAudit,
# CodeAudit, AIResearchScan) -- python's own -u flag disables output
# buffering so a hang still flushes whatever ran before it.
$Action  = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$PythonExe`" -u `"$ScriptPath`" >> `"$LogPath`" 2>&1`"" `
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

# S4U (run whether the user is logged on or not), not the Register-
# ScheduledTask default of Interactive -- explicit here so re-running this
# script later (e.g. to tweak the schedule) can never silently regress it
# back to Interactive the way Leviathan-SubscriberReport and
# Leviathan-WeeklyAudit both did (found in a 2026-08-17 audit).
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
Write-Host "Task '$TaskName' registered. Runs daily at 9:15am."
Write-Host "Output logs to $LogPath"
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

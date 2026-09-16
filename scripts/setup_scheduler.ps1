# Registers a Windows Task Scheduler job to run the daily smart money scan.
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1

$TaskName   = "Leviathan-SmartMoneyScan"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\daily_smart_money.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent
$LogPath    = "$WorkDir\logs\smart_money_scan.log"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# cmd.exe wrapper + output redirection (2026-09-16, backlog:
# smtp-send-no-timeout-hang-2026-09's own follow-up) -- this task was one
# of two (with PositionReconciliation) that hung their full
# ExecutionTimeLimit during the 2026-09-13 systemic SSL/TLS incident with
# no stdout/stderr captured at all, so the real hang point was never
# directly observed, only inferred (see reports/code_audits/2026-09-13.md).
# Same pattern every other Leviathan-* task with output capture already
# uses (GateNotifier, WeeklyAudit, CodeAudit, AIResearchScan) -- python's
# own -u flag disables output buffering so a hang still flushes whatever
# ran before it, instead of losing everything to a buffer that never gets
# flushed before Task Scheduler kills the process. $LogPath above was
# previously declared but never actually wired into $Action -- dead code
# from an earlier, different logging scheme; using the same logs\<name>.log
# convention every other task now uses instead of reviving the old
# data\smart_money\scheduler.log path.
$Action  = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$PythonExe`" -u `"$ScriptPath`" >> `"$LogPath`" 2>&1`"" `
    -WorkingDirectory $WorkDir

# Run daily at 8:07am local time
$Trigger = New-ScheduledTaskTrigger -Daily -At "08:07AM"

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
Write-Host "Task '$TaskName' registered. Runs daily at 8:07am."
Write-Host "Output logs to $LogPath"
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

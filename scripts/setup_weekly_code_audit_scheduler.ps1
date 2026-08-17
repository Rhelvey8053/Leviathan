# Registers a Windows Task Scheduler job for the weekly, report-only
# Claude Code system-and-code audit (scripts/weekly_code_audit.py).
# Read-only: never edits code, never touches Task Scheduler or a live
# process, never touches data/leviathan.db, never commits/pushes --
# writes findings to reports/code_audits/<date>.md for a human to review.
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_weekly_code_audit_scheduler.ps1

$TaskName   = "Leviathan-CodeAudit"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\weekly_code_audit.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent
$LogPath    = "$WorkDir\logs\weekly_code_audit.log"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# cmd.exe wrapper + output redirection -- same pattern every other
# Leviathan-* task uses (Leviathan-SubscriberReport, Leviathan-WeeklyAudit)
# for a Task-Scheduler-specific hang seen with unredirected output.
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$PythonExe`" `"$ScriptPath`" >> `"$LogPath`" 2>&1`"" `
    -WorkingDirectory $WorkDir

# Weekly, Sunday 11:00am local -- one hour after Leviathan-WeeklyAudit's
# 10:00am slot, so the calibration audit has finished (it's typically a
# few minutes, capped at 30) before this one starts. Same quiet-morning
# reasoning: clear of the daily 6-9:15am cluster.
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "11:00AM"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# S4U (run whether the user is logged on or not), not the Register-
# ScheduledTask default of Interactive -- explicit here so this task
# starts correct, and so re-running this script later (e.g. to tweak the
# schedule) can never silently regress it back to Interactive the way
# Leviathan-SubscriberReport and Leviathan-WeeklyAudit both did (their
# setup scripts never specified a Principal at all -- found in a
# 2026-08-17 audit; those two scripts still need the same fix).
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action    $Action `
    -Trigger   $Trigger `
    -Settings  $Settings `
    -Principal $Principal `
    -Force

Write-Host ""
Write-Host "Task '$TaskName' registered. Runs weekly, Sunday 11:00am, logon type S4U."
Write-Host "Findings land in reports\code_audits\<date>.md; raw output logs to $LogPath"
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

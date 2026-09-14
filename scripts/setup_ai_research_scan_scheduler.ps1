# Registers a Windows Task Scheduler job for the weekly, report-only
# Claude Code AI/GitHub research scan (scripts/ai_research_scan.py).
# Read-only: never edits code, never touches Task Scheduler or a live
# process, never touches data/leviathan.db, never commits/pushes, never
# files anything to backlog/backlog.json itself -- writes findings to
# reports/ai_research/<date>.md for a human (or PM review) to act on.
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_ai_research_scan_scheduler.ps1

$TaskName   = "Leviathan-AIResearchScan"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\ai_research_scan.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent
$LogPath    = "$WorkDir\logs\ai_research_scan.log"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# cmd.exe wrapper + output redirection -- same pattern every other
# Leviathan-* task uses (Leviathan-WeeklyAudit, Leviathan-CodeAudit) for a
# Task-Scheduler-specific hang seen with unredirected output.
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$PythonExe`" `"$ScriptPath`" >> `"$LogPath`" 2>&1`"" `
    -WorkingDirectory $WorkDir

# Weekly, Sunday 12:20pm local -- after Leviathan-CodeAudit's 11:00am/65min
# slot (ends by ~12:05pm) so this doesn't contend with it, and clear of
# Leviathan-WakeCatchup's 12:00pm fallback trigger. Web research (multiple
# searches/fetches) is I/O-bound, not CPU-bound, so running alongside the
# quiet Sunday morning cluster is fine -- it just shouldn't start before
# the other two Sunday audits have wrapped up.
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "12:20PM"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 45) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries

# S4U (run whether the user is logged on or not) with an explicit
# Principal from the start -- matches Leviathan-CodeAudit's corrected
# pattern (see that task's own setup script comment: Leviathan-
# WeeklyAudit/-SubscriberReport originally shipped with no Principal at
# all and silently regressed to Interactive on any re-run).
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
Write-Host "Task '$TaskName' registered. Runs weekly, Sunday 12:20pm, logon type S4U."
Write-Host "Findings land in reports\ai_research\<date>.md; raw output logs to $LogPath"
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

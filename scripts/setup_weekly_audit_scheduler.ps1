# Registers a Windows Task Scheduler job for the weekly, investigate-only
# Claude Code audit (scripts/weekly_audit.py). Read-only: never edits code,
# never touches data/leviathan.db, never commits/pushes -- writes findings
# to reports/audits/<date>.md for a human to review.
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_weekly_audit_scheduler.ps1

$TaskName   = "Leviathan-WeeklyAudit"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\weekly_audit.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent
$LogPath    = "$WorkDir\logs\weekly_audit.log"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# cmd.exe wrapper + output redirection -- same fix as Leviathan-SubscriberReport
# for a Task-Scheduler-specific hang seen earlier with unredirected output.
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$PythonExe`" `"$ScriptPath`" >> `"$LogPath`" 2>&1`"" `
    -WorkingDirectory $WorkDir

# Weekly, Sunday 10:00am local -- clear of the daily 6-9:15am cluster, and a
# claude --print investigative run can take several minutes so a quiet
# morning slot avoids contending with the daily pipeline.
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "10:00AM"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# S4U (run whether the user is logged on or not), not the Register-
# ScheduledTask default of Interactive -- explicit here so re-running this
# script later (e.g. to tweak the schedule) can never silently regress it
# back to Interactive the way this exact task (and Leviathan-SubscriberReport)
# both did in practice: this script never specified a Principal at all,
# so a re-run always fell back to Interactive -- found in a 2026-08-17 audit.
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
Write-Host "Task '$TaskName' registered. Runs weekly, Sunday 10:00am."
Write-Host "Findings land in reports\audits\<date>.md; raw output logs to $LogPath"
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

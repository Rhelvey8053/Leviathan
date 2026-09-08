# Registers a Windows Task Scheduler job for the PnL integrity check
# (scripts/verify_pnl.py). Read-only against the DB: runs WITHOUT --apply,
# so it can only ever detect and alert on drift, never write it -- a
# human reviews and re-runs with --apply by hand if it finds something.
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_verify_pnl_scheduler.ps1
#
# Not previously on any schedule -- this check only ever ran when someone
# happened to invoke it by hand (2026-09-08 finding). Weekly is enough:
# PnL drift only happens if resolve_outcomes()'s formula itself changes
# and old rows aren't backfilled, not from routine day-to-day operation.

$TaskName   = "Leviathan-VerifyPnL"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\verify_pnl.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent
$LogPath    = "$WorkDir\logs\verify_pnl.log"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# cmd.exe wrapper + output redirection -- same fix as every other
# Leviathan-* task for a Task-Scheduler-specific hang with unredirected
# output.
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$PythonExe`" `"$ScriptPath`" >> `"$LogPath`" 2>&1`"" `
    -WorkingDirectory $WorkDir

# Weekly, Sunday 10:30am local -- right after Leviathan-WeeklyAudit (10:00am)
# so both weekly checks land in the same quiet morning window without
# overlapping (WeeklyAudit's own claude --print run can take several
# minutes).
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "10:30AM"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# S4U (run whether the user is logged on or not) -- explicit here so
# re-running this script later can never silently regress it back to
# Interactive the way Leviathan-SubscriberReport and Leviathan-WeeklyAudit
# both did once (found in a 2026-08-17 audit).
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
Write-Host "Task '$TaskName' registered. Runs weekly, Sunday 10:30am."
Write-Host "Sends an alert email only if drift is found; otherwise logs OK to $LogPath"
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

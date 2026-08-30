# Leviathan -- Windows Task Scheduler Setup
# Run this once as Administrator to schedule daily runs.
# Default: every day at 7:00 AM local time.
#
# Usage:
#   Right-click PowerShell → "Run as Administrator"
#   cd C:\Users\Administrator\Downloads\Leviathan
#   .\schedule_setup.ps1
#
# To change the time, edit $RunTime below.

$TaskName   = "Leviathan-DailyRun"
# 2026-08-25: $MyInvocation.MyCommand.Path always resolves to this file's
# real on-disk location (scripts\schedule_setup.ps1) regardless of the
# caller's cwd, despite the usage comment above implying a project-root
# invocation -- main.py itself lives one level up, at the project root,
# not alongside this script. Using $ScriptDir directly here previously
# pointed the registered task at a nonexistent scripts\main.py (confirmed
# live via Get-ScheduledTask after a re-registration run). Matches the
# $WorkDir = Split-Path $PSScriptRoot -Parent pattern every other
# setup_*.ps1 in this directory already uses.
$ScriptDir  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonExe  = (Get-Command python -ErrorAction SilentlyContinue).Source
$MainScript = Join-Path $ScriptDir "main.py"
# Under logs/ (not repo root) to match every other Leviathan-* task's log
# location and stay covered by .gitignore's `logs/` rule -- the old bare
# root-level path wasn't ignored at all and risked a stray untracked file
# getting swept into a careless `git add -A`.
$LogFile    = Join-Path $ScriptDir "logs\leviathan_scheduler.log"
$RunTime    = "07:00"   # 24-hour local time

if (-not $PythonExe) {
    Write-Error "Python not found in PATH. Install Python and try again."
    exit 1
}

# Remove existing task if it exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# cmd.exe wrapper + output redirection added 2026-08-30 -- $LogFile was
# defined above but never actually used; DailyRun was the only Leviathan-*
# task still launching python directly with no output capture at all.
# Confirmed both costs of that gap the same day: (1) a real Kalshi
# auth-failure run produced zero diagnostic trace anywhere (Task Scheduler
# reported clean exit 0, the actual error message was gone), and (2)
# setup_weekly_code_audit_scheduler.ps1's own comment already documents
# unredirected output as a known Task-Scheduler-specific hang cause on
# this machine -- the same reason Leviathan-SubscriberReport/WeeklyAudit
# already use this cmd.exe + >> pattern. Matches that pattern exactly.
#
# `-u` (unbuffered stdout/stderr) added the same day after a live smoke
# test: a manually-triggered run that was force-stopped (Stop-ScheduledTask)
# left the log file at 0 bytes despite the process having already printed
# several steps' worth of output -- Python fully buffers stdout by default
# once it's redirected to a file/pipe instead of a real console, so nothing
# is written until the buffer fills or the process exits cleanly. That's
# exactly backwards for the case this log file most needs to cover:
# main.py DOES exit cleanly on a normal early-return (today's actual
# incident would have been captured fine even buffered), but
# ExecutionTimeLimit (2 hours, set below) or ANY other forced termination
# would silently lose everything printed up to that point. -u makes every
# print() flush immediately, so a forced kill still leaves a real trail.
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$PythonExe`" -u `"$MainScript`" >> `"$LogFile`" 2>&1`"" `
    -WorkingDirectory $ScriptDir

$Trigger = New-ScheduledTaskTrigger -Daily -At $RunTime

# RestartCount/RestartInterval added 2026-08-25: main.py now sys.exit(1)s
# specifically when markets were flagged but the Claude CLI scoring call
# hard-failed (e.g. a Claude usage-limit exhaustion) -- see the
# scoring_hard_failed comment in main.py. 1 hour is a guess, not a
# confirmed number: there is no CLI/API-exposed way to query this
# account's actual usage-reset window, so this may retry too early or
# later than strictly needed. 3 attempts, 1 hour apart, covers roughly a
# 3-hour window past the original 7:00 AM run.
# Battery tolerance added 2026-08-27: this task defaulted to
# DisallowStartIfOnBatteries/StopIfGoingOnBatteries = True (refuses to
# start, or gets killed mid-run, on battery power) -- same footgun class
# already fixed for Leviathan-CodeAudit (setup_weekly_code_audit_scheduler.ps1)
# but never applied to DailyRun's own setup script until now. Correct
# parameter names confirmed live: -DontStopIfGoingOnBatteries and
# -AllowStartIfOnBatteries (switches, not the differently-named/inverted
# -StopIfGoingOnBatteries:$false / -DisallowStartIfOnBatteries:$false a
# first attempt on CodeAudit's fix incorrectly used).
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Hours 1) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries

# S4U (2026-08-27): DailyRun was the last Leviathan-* task still on
# Interactive logon (can't fire if the account is logged off, only if
# merely asleep/locked) -- every other setup_*.ps1 in this directory
# already uses S4U, which runs regardless of logon state. Matches
# setup_weekly_code_audit_scheduler.ps1's exact pattern (domain-qualified
# UserId, not bare $env:USERNAME).
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
    -Description "Leviathan prediction market scanner -- daily run" | Out-Null

Write-Host ""
Write-Host "Scheduled task created: $TaskName"
Write-Host "Runs daily at:          $RunTime"
Write-Host "Script:                 $MainScript"
Write-Host "Working directory:      $ScriptDir"
Write-Host ""
Write-Host "To view:   Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host "To run now: Start-ScheduledTask -TaskName '$TaskName'"

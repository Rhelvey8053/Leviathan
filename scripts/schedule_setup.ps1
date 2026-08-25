# Leviathan — Windows Task Scheduler Setup
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
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe  = (Get-Command python -ErrorAction SilentlyContinue).Source
$MainScript = Join-Path $ScriptDir "main.py"
$LogFile    = Join-Path $ScriptDir "leviathan_scheduler.log"
$RunTime    = "07:00"   # 24-hour local time

if (-not $PythonExe) {
    Write-Error "Python not found in PATH. Install Python and try again."
    exit 1
}

# Remove existing task if it exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$MainScript`"" `
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
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Hours 1) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $Action `
    -Trigger   $Trigger `
    -Settings  $Settings `
    -Principal $Principal `
    -Description "Leviathan prediction market scanner — daily run" | Out-Null

Write-Host ""
Write-Host "Scheduled task created: $TaskName"
Write-Host "Runs daily at:          $RunTime"
Write-Host "Script:                 $MainScript"
Write-Host "Working directory:      $ScriptDir"
Write-Host ""
Write-Host "To view:   Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host "To run now: Start-ScheduledTask -TaskName '$TaskName'"

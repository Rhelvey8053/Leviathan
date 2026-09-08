# Registers a Windows Task Scheduler job to run the monday.com board sync.
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_monday_sync_scheduler.ps1

$TaskName   = "Leviathan-MondaySync"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\monday_sync.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# --phase3 runs the full sync + progress log; --live is explicit (never
# relies on config.json's monday.dry_run_default, which a manual run
# without either flag falls back to -- the scheduled task should never
# have its behavior silently flipped by a config edit made for a
# different reason). --once is accepted as a documented no-op (this
# script always runs once per invocation and exits).
$Action  = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$ScriptPath`" --phase3 --live --once" `
    -WorkingDirectory $WorkDir

# Run daily at 9:00am local time -- after the 8:45am gate-notifier (per
# the monday-sync handoff's own instruction: "runs after the gate checks,
# so it syncs freshly computed state") and before the 9:15am position-
# reconciliation job.
$Trigger = New-ScheduledTaskTrigger -Daily -At "09:00AM"

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
Write-Host "Task '$TaskName' registered. Runs daily at 9:00am (live sync + progress log)."
Write-Host "To run immediately:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To preview first:    python scripts\monday_sync.py --phase3 --dry-run"
Write-Host "To remove:           Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host "Runbook:             docs\monday_sync_runbook.md"

# Registers a Windows Task Scheduler job for the automation health check
# (scheduled-task drift + Litestream replica lag).
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_automation_health_scheduler.ps1
#
# Runs once daily, well after every other daily task's own scheduled time
# (last one, Leviathan-SubscriberReport, fires at 9am) so a normal day's
# tasks have long since either landed or not by the time this checks.

$TaskName   = "Leviathan-AutomationHealthCheck"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\automation_health_check.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $WorkDir

$Trigger = New-ScheduledTaskTrigger -Daily -At "03:00PM"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# S4U (run whether the user is logged on or not) -- explicit here so
# re-running this script later can never silently regress it back to
# Interactive the way Leviathan-SubscriberReport and Leviathan-WeeklyAudit
# both did (found in a 2026-08-17 audit).
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
Write-Host "Task '$TaskName' registered. Runs daily at 3:00pm."
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

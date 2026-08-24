# Registers a Windows Task Scheduler job that fires when the machine wakes
# from sleep, and re-launches any Leviathan-* task that was missed while
# asleep (scripts/catchup_missed_tasks.py).
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_wake_catchup_scheduler.ps1
#
# 2026-08-24: the machine slept through Leviathan-DailyRun's 6am trigger
# and, despite StartWhenAvailable=True, Task Scheduler never actually
# caught it up on wake. Bound to the Kernel-Power wake event (ID 1)
# rather than "At log on" -- this machine's console session stays
# "Active" through sleep/resume without necessarily generating a fresh
# logon event, so AtLogOn would miss exactly the case this exists to
# catch.
#
# Not end-to-end verified from this session -- an automated session can't
# put the machine to sleep and wake it. The next real sleep/wake cycle is
# the actual test; check logs\catchup_missed_tasks.log afterward.

$TaskName   = "Leviathan-WakeCatchup"
$PythonExe  = (Get-Command python).Source
$ScriptPath = "$PSScriptRoot\catchup_missed_tasks.py"
$WorkDir    = Split-Path $PSScriptRoot -Parent
$LogPath    = "$WorkDir\logs\catchup_missed_tasks.log"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# cmd.exe wrapper + output redirection -- same pattern as WeeklyAudit/
# SubscriberReport (fixes a Task-Scheduler-specific hang seen earlier
# with unredirected output).
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$PythonExe`" `"$ScriptPath`" >> `"$LogPath`" 2>&1`"" `
    -WorkingDirectory $WorkDir

# Event trigger bound to Kernel-Power Event ID 1 (system resumed from
# sleep) -- New-ScheduledTaskTrigger has no friendly "on wake" parameter,
# so this is built directly via the CIM event-trigger class. A 3-minute
# delay lets network/DB/etc. actually come back up before anything tries
# to run against them.
$CIMTriggerClass = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
$Trigger = New-CimInstance -CimClass $CIMTriggerClass -ClientOnly
$Trigger.Subscription = @'
<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and (EventID=1)]]</Select></Query></QueryList>
'@
$Trigger.Delay = "PT3M"
$Trigger.Enabled = $true

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

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
Write-Host "Task '$TaskName' registered. Fires ~3 min after the machine wakes from sleep."
Write-Host "Output logs to $LogPath"
Write-Host "To test now (simulates a wake-triggered run directly, bypassing the event): Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

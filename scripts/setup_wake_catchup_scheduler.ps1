# Registers a Windows Task Scheduler job that re-launches any Leviathan-*
# task that was missed (scripts/catchup_missed_tasks.py), on three
# separate triggers: wake-from-sleep, logon (covers a full shutdown ->
# cold boot, which a sleep/wake event trigger alone would miss), and a
# fixed daily time (covers "the laptop was just left on and awake" --
# neither of the other two triggers fires in that case at all, so
# without this a stale task could sit unfixed indefinitely on a machine
# that's never actually slept or been logged out of).
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_wake_catchup_scheduler.ps1
#
# 2026-08-24: the machine slept through Leviathan-DailyRun's 6am trigger
# and, despite StartWhenAvailable=True, Task Scheduler never actually
# caught it up on wake. The wake trigger is bound to the Kernel-Power
# event (ID 1) rather than relying on AtLogOn alone -- this machine's
# console session stays "Active" through sleep/resume without
# necessarily generating a fresh logon event, so AtLogOn on its own
# would miss exactly that case. AtLogOn is kept as a second trigger
# specifically for the cold-boot case a wake event can't cover.
#
# Not end-to-end verified from this session -- an automated session can't
# put the machine to sleep and wake it, or log it out and back in. The
# next real sleep/wake cycle and the next real logon are the actual
# tests; check logs\catchup_missed_tasks.log afterward. The daily-time
# trigger IS directly verifiable (Start-ScheduledTask), same as any other
# fixed-schedule task.

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

# Trigger 1: Kernel-Power Event ID 1 (system resumed from sleep) --
# New-ScheduledTaskTrigger has no friendly "on wake" parameter, so this
# is built directly via the CIM event-trigger class. A 3-minute delay
# lets network/DB/etc. actually come back up before anything tries to
# run against them.
$CIMTriggerClass = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
$WakeTrigger = New-CimInstance -CimClass $CIMTriggerClass -ClientOnly
$WakeTrigger.Subscription = @'
<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and (EventID=1)]]</Select></Query></QueryList>
'@
$WakeTrigger.Delay = "PT3M"
$WakeTrigger.Enabled = $true

# Trigger 2: at logon -- covers a full shutdown -> cold boot, which the
# wake-event trigger above doesn't fire for at all. Same 3-minute delay
# rationale.
$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn
$LogonTrigger.Delay = "PT3M"

# Trigger 3: fixed daily time -- covers the case where the machine is
# just left on and awake all day, so neither trigger above ever fires.
# Scheduled after the morning task cluster (last one, PositionReconciliation,
# runs ~9:15am) and before AutomationHealthCheck's 3pm alert pass, so a
# genuinely-missed task has a real chance to get fixed before it's
# flagged rather than the two running as pure duplicates of each other.
$DailyTrigger = New-ScheduledTaskTrigger -Daily -At "12:00PM"

$Triggers = @($WakeTrigger, $LogonTrigger, $DailyTrigger)

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
    -Trigger   $Triggers `
    -Settings  $Settings `
    -Principal $Principal `
    -Force

Write-Host ""
Write-Host "Task '$TaskName' registered. Fires ~3 min after wake-from-sleep, ~3 min after logon, and daily at 12:00pm."
Write-Host "Output logs to $LogPath"
Write-Host "To test now (runs the check directly, bypassing all three triggers): Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

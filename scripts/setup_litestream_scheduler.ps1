# Registers a Windows Task Scheduler job that runs Litestream continuously
# (not a daily batch job like the other Leviathan-* tasks) to WAL-stream
# data/leviathan.db to a local-folder replica at
# data/db_backups/litestream_replica/. Restore with:
#   tools\litestream.exe restore -config tools\litestream.yml -o <output> data\leviathan.db
#
# Verified 2026-08-02: replicate -> restore round-trip produces a byte-exact
# row count match against the live DB (271/68/12600 rows).
#
# Run once as Administrator: powershell -ExecutionPolicy Bypass -File scripts\setup_litestream_scheduler.ps1

$TaskName    = "Leviathan-Litestream"
$WorkDir     = Split-Path $PSScriptRoot -Parent
$LitestreamExe = "$WorkDir\tools\litestream.exe"
$ConfigPath  = "$WorkDir\tools\litestream.yml"
$LogPath     = "$WorkDir\logs\litestream.log"

if (-not (Test-Path $LitestreamExe)) {
    Write-Host "litestream.exe not found at $LitestreamExe" -ForegroundColor Red
    exit 1
}

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# cmd.exe wrapper + output redirection, same pattern as the other tasks --
# litestream logs continuously to stdout, useful for confirming it's alive.
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$LitestreamExe`" replicate -config `"$ConfigPath`" >> `"$LogPath`" 2>&1`"" `
    -WorkingDirectory "$WorkDir\tools"

# At log on -- runs as the logged-in Administrator (matches file ownership
# under this user's Downloads folder), not At-startup/SYSTEM.
$Trigger = New-ScheduledTaskTrigger -AtLogOn

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

# S4U (run whether the user is logged on or not) rather than the Register-
# ScheduledTask default of Interactive -- explicit here so re-running this
# script later (e.g. to tweak settings) can never silently regress it back
# to Interactive the way Leviathan-SubscriberReport and
# Leviathan-WeeklyAudit both did (found in a 2026-08-17 audit). Combined
# with the AtLogOn trigger above, this still only starts replicate when
# this user logs on -- S4U vs Interactive governs the credential/token
# type the process runs under, not when the trigger fires.
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
Write-Host "Task '$TaskName' registered. Runs continuously from next log-on."
Write-Host "To start it now:    Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To check it's alive: Get-ScheduledTask -TaskName '$TaskName' | Select State"
Write-Host "To stop it:          Stop-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:           Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"

# Launches Datasette (https://datasette.io/) as a local, read-only web UI +
# JSON API over data/leviathan.db, for ad-hoc interactive exploration
# (signals, settled_markets, etc.) without writing one-off `py -c` scripts.
#
# Not a scheduled task -- run this manually whenever you want to browse the
# data. Binds to 127.0.0.1 only (default), so it's never reachable from
# outside this machine. Datasette's own query UI only permits SELECT by
# default, so this can't accidentally write to the DB.
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\launch_datasette.ps1
# Then open http://127.0.0.1:8001 in a browser. Ctrl+C to stop.

$WorkDir = Split-Path $PSScriptRoot -Parent
$DbPath  = "$WorkDir\data\leviathan.db"

if (-not (Test-Path $DbPath)) {
    Write-Host "Database not found at $DbPath" -ForegroundColor Red
    exit 1
}

Write-Host "Starting Datasette on http://127.0.0.1:8001 -- Ctrl+C to stop"
py -m datasette serve $DbPath --port 8001 --open

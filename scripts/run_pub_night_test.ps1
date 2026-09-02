# Runs the pub-night dry run, then the real 5-hour run. No shutdown at the
# end (removed on request) -- the machine stays on so you can look at the
# results yourself. Run this in its own PowerShell window (not through an
# agent's background process) so it keeps running independent of any
# editor/chat session for the full ~5 hours.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\run_pub_night_test.ps1
#
# Safety notes, read before running:
#   - The dry run must finish cleanly (no crash) before the real run
#     starts. A "WATCH" or "FAIL" verdict from a real found bug still lets
#     it continue -- that's the point of the test. Only a hard crash stops
#     the chain here.
#   - There's a 15-second cancel window before the real 5-hour run starts --
#     Ctrl+C to stop.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "=== Dry run (~6 minutes) ===" -ForegroundColor Cyan
& .venv\Scripts\python.exe manage.py pub_night_test --hours 0.1
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nDry run CRASHED (exit $LASTEXITCODE) -- stopping here." -ForegroundColor Red
    Write-Host "Not running the real 5-hour test or shutting down. Fix the crash first." -ForegroundColor Red
    exit 1
}

Write-Host "`nDry run finished without crashing." -ForegroundColor Green
Write-Host "Check the verdict above (and the log in loadtest_logs\) before trusting this run." -ForegroundColor Yellow
Write-Host "Starting the real 5-hour run in 15 seconds... Ctrl+C now to cancel." -ForegroundColor Yellow
Start-Sleep -Seconds 15

Write-Host "`n=== Real run (5 hours) ===" -ForegroundColor Cyan
& .venv\Scripts\python.exe manage.py pub_night_test --hours 5

Write-Host "`nReal run finished. Check loadtest_logs\ for the full result and verdict." -ForegroundColor Green

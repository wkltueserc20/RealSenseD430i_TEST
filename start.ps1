# start.ps1 - launch the unified vision system (Python backend + web UI)
# Usage:  powershell -ExecutionPolicy Bypass -File .\start.ps1
$root = $PSScriptRoot

Write-Host "Starting vision backend (Python)..." -ForegroundColor Cyan
Write-Host "First run loads models + warmup (~15s), then the browser UI opens automatically." -ForegroundColor Yellow
Write-Host "UI: http://localhost:8000/   (same LAN: http://<this-PC-IP>:8000/)" -ForegroundColor Green

Set-Location $root
python vision_server.py

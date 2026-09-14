#Requires -Version 5.1
param([switch]$Build, [switch]$SkipTools)
$ErrorActionPreference = 'Stop'
$dailyRoot = Split-Path -Parent $PSScriptRoot
$dailyPython = Join-Path $dailyRoot '.conda/python.exe'
if (-not (Test-Path -LiteralPath $dailyPython)) { throw 'Missing .conda/python.exe; install the project environment first.' }
Set-Location -LiteralPath $dailyRoot
$dailyLogs = Join-Path $dailyRoot 'data/logs'
New-Item -ItemType Directory -Force -Path $dailyLogs | Out-Null
if (-not $SkipTools) {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        $dailyDocker = Join-Path $env:ProgramFiles 'Docker/Docker/Docker Desktop.exe'
        if (-not (Test-Path -LiteralPath $dailyDocker)) { throw 'Docker Desktop is missing. Use -SkipTools for chat/control only.' }
        Start-Process -FilePath $dailyDocker -WindowStyle Hidden
        $dailyDeadline = (Get-Date).AddSeconds(120)
        do { Start-Sleep -Seconds 2; docker info *> $null } until ($LASTEXITCODE -eq 0 -or (Get-Date) -gt $dailyDeadline)
        if ($LASTEXITCODE -ne 0) { throw 'Docker did not become ready.' }
    }
    if ($Build) { docker compose up -d --build } else { docker compose up -d }
    if ($LASTEXITCODE -ne 0) { throw 'Tool containers failed to start.' }
}
# Cloud-only use does not require Ollama. Existing provider settings select the model.
$dailyListener = Get-NetTCPConnection -LocalPort 9510 -State Listen -ErrorAction SilentlyContinue
if ($dailyListener) {
    $dailyStatus = Invoke-RestMethod 'http://127.0.0.1:9510/api/status' -TimeoutSec 5
    if (-not $dailyStatus.daily_services) { throw 'Port 9510 belongs to another launch mode. Close it from Web before starting daily mode.' }
} else {
    Start-Process -FilePath $dailyPython -ArgumentList @('scripts/run_daily.py') -WorkingDirectory $dailyRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $dailyLogs 'daily-bff.log') -RedirectStandardError (Join-Path $dailyLogs 'daily-bff.err.log')
}
$dailyReady = $false
for ($dailyAttempt=0; $dailyAttempt -lt 60; $dailyAttempt++) {
    try { $dailyStatus = Invoke-RestMethod 'http://127.0.0.1:9510/api/status' -TimeoutSec 2; $dailyReady = [bool]$dailyStatus.daily_services } catch {}
    if ($dailyReady) { break }
    Start-Sleep -Seconds 1
}
if (-not $dailyReady) { throw 'Daily BFF is not ready. Check data/logs/daily-bff.err.log.' }
if (-not (Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)) {
    $dailyNode = (Get-Command node -ErrorAction Stop).Source
    $dailyVite = Join-Path $dailyRoot 'apps/web/node_modules/vite/bin/vite.js'
    if (-not (Test-Path -LiteralPath $dailyVite)) { throw 'Install frontend dependencies in apps/web first.' }
    Start-Process -FilePath $dailyNode -ArgumentList @('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173','--strictPort') -WorkingDirectory (Join-Path $dailyRoot 'apps/web') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $dailyLogs 'daily-web.log') -RedirectStandardError (Join-Path $dailyLogs 'daily-web.err.log')
}
Start-Process 'http://127.0.0.1:5173'
Write-Output 'Daily Web: http://127.0.0.1:5173 — manage stock services in the right sidebar.'

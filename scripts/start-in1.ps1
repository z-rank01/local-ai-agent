#Requires -Version 5.1
param([switch]$Build)
$ErrorActionPreference = 'Stop'
$in1Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $in1Root
$in1Python = Join-Path $in1Root '.conda/python.exe'
if (-not (Test-Path -LiteralPath $in1Python)) { throw 'Install the project Python environment first; see docs/IN1运行与验证.md.' }
foreach ($in1Dir in @('workspace','logs','trash')) { New-Item -ItemType Directory -Force -Path (Join-Path $in1Root "data/in1/$in1Dir") | Out-Null }
if ($Build) { docker compose -f compose.in1.yml up -d --build } else { docker compose -f compose.in1.yml up -d }
if ($LASTEXITCODE -ne 0) { throw 'IN1 Docker services failed to start' }
$in1Existing = Get-NetTCPConnection -LocalPort 9510 -State Listen -ErrorAction SilentlyContinue
if ($in1Existing) {
    $in1Status = Invoke-RestMethod 'http://127.0.0.1:9510/api/status'
    if ($in1Status.workspace_path -ne (Join-Path $in1Root 'data/in1/workspace')) { throw 'Port 9510 is used by another workspace; stop that BFF first.' }
} else {
    Start-Process -FilePath $in1Python -ArgumentList 'scripts/run_in1.py' -WorkingDirectory $in1Root -WindowStyle Hidden -RedirectStandardOutput "$in1Root/data/in1/logs/bff.out.log" -RedirectStandardError "$in1Root/data/in1/logs/bff.err.log"
}
if (-not (Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)) {
    $in1Node = (Get-Command node).Source
    Start-Process -FilePath $in1Node -ArgumentList 'node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173','--strictPort' -WorkingDirectory "$in1Root/apps/web" -WindowStyle Hidden -RedirectStandardOutput "$in1Root/data/in1/logs/web.out.log" -RedirectStandardError "$in1Root/data/in1/logs/web.err.log"
}
Write-Output 'IN1 Web: http://127.0.0.1:5173 (model settings are at the top)'

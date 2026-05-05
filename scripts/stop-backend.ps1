#Requires -Version 5.1
param(
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
$envFile = Join-Path $projectRoot ".env"

function Write-Ok([string]$msg) { Write-Host "   [OK] $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "   [WARN] $msg" -ForegroundColor Yellow }

function Get-EnvValue {
    param(
        [string]$FilePath,
        [string]$Key,
        [string]$Default
    )
    try {
        $match = Select-String -Path $FilePath -Pattern ("^" + [regex]::Escape($Key) + "=(.+)$") -ErrorAction SilentlyContinue
        if ($match -and $match.Matches.Count -gt 0) {
            return $match.Matches[0].Groups[1].Value.Trim()
        }
    } catch {}
    return $Default
}

function Get-ListeningProcessId {
    param([int]$Port)
    try {
        $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($connection) { return [int]$connection.OwningProcess }
    } catch {}
    return $null
}

if ($Port -le 0) {
    $Port = [int](Get-EnvValue -FilePath $envFile -Key "BFF_PORT" -Default "9510")
}

Write-Host ""
Write-Host ":: Stopping Python BFF only" -ForegroundColor Cyan
$processId = Get-ListeningProcessId -Port $Port
if (-not $processId) {
    Write-Warn "No Python BFF listener found on port $Port"
    exit 0
}

Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500
Write-Ok "Stopped Python BFF process $processId on port $Port"
Write-Host "   Docker containers and Ollama were not touched." -ForegroundColor DarkGray
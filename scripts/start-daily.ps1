#Requires -Version 5.1
# 唯一日常入口：准备环境 → 启动工具容器与聊天服务（含网页）→ 打开浏览器。
# 已运行时重复执行只会打开浏览器，不会重复启动服务。
param([switch]$Build, [switch]$SkipTools, [switch]$RebuildWeb)
$ErrorActionPreference = 'Stop'

function Write-Step([string]$Message) { Write-Host "      $Message" -ForegroundColor DarkGray }
function Write-Ok([string]$Message) { Write-Host "      [OK] $Message" -ForegroundColor Green }
function Write-Warn2([string]$Message) { Write-Host "      [!!] $Message" -ForegroundColor Yellow }

$dailyRoot = Split-Path -Parent $PSScriptRoot
$dailyPython = Join-Path $dailyRoot '.conda/python.exe'
if (-not (Test-Path -LiteralPath $dailyPython)) {
    throw 'Missing .conda\python.exe. Create it first: python -m venv .conda; .\.conda\python.exe -m pip install -e .'
}
Set-Location -LiteralPath $dailyRoot
$dailyLogs = Join-Path $dailyRoot 'data/logs'
New-Item -ItemType Directory -Force -Path $dailyLogs | Out-Null

Write-Host ''
Write-Host '  Local AI Agent - starting' -ForegroundColor White
Write-Host ''

# -- [0/5] workspace ---------------------------------------------------------
$workspace = Join-Path $dailyRoot 'data/workspace'
if (-not (Test-Path -LiteralPath (Join-Path $workspace '.git'))) {
    Write-Host '  [0/5] Workspace' -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $workspace | Out-Null
    foreach ($d in @('data', 'docs', 'reports', 'skills')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $workspace $d) | Out-Null
    }
    Push-Location $workspace
    try {
        git init -b main *> $null
        git config user.name 'Local AI Agent'
        git config user.email 'local-agent@example.local'
        git add . 2>$null
        git commit -m 'chore: initialize workspace' *> $null
    } finally { Pop-Location }
    Write-Ok 'workspace initialized (data/workspace)'
}

# -- [1/5] Docker + tool containers ------------------------------------------
$dailyEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    if (-not $SkipTools) {
        Write-Host '  [1/5] Docker & tool containers' -ForegroundColor Cyan
        docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Step 'starting Docker Desktop...'
            $dailyDocker = Join-Path $env:ProgramFiles 'Docker/Docker/Docker Desktop.exe'
            if (-not (Test-Path -LiteralPath $dailyDocker)) { throw 'Docker Desktop is missing. Use -SkipTools for chat/control only.' }
            Start-Process -FilePath $dailyDocker -WindowStyle Hidden
            $dailyDeadline = (Get-Date).AddSeconds(120)
            do { Start-Sleep -Seconds 2; docker info *> $null } until ($LASTEXITCODE -eq 0 -or (Get-Date) -gt $dailyDeadline)
            if ($LASTEXITCODE -ne 0) { throw 'Docker did not become ready.' }
        }
        Write-Ok 'Docker is ready'
        if ($Build) { docker compose up -d --build *> $null } else { docker compose up -d *> $null }
        if ($LASTEXITCODE -ne 0) { throw 'Tool containers failed to start.' }
        Write-Ok 'containers up (skill-files, skill-runner; web search starts from the page toggle)'
    }

    # -- [2/5] Web build ------------------------------------------------------
    Write-Host '  [2/5] Web UI' -ForegroundColor Cyan
    $dailyIndex = Join-Path $dailyRoot 'apps/web/dist/index.html'
    if ($RebuildWeb -or -not (Test-Path -LiteralPath $dailyIndex)) {
        Write-Step 'building apps/web (first time may take a minute)...'
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'npm is required to build the Web UI. Install Node.js first.' }
        if (-not (Test-Path -LiteralPath (Join-Path $dailyRoot 'apps/web/node_modules'))) {
            npm ci --prefix (Join-Path $dailyRoot 'apps/web') *> $null
            if ($LASTEXITCODE -ne 0) { throw 'Web dependency install failed.' }
        }
        npm run build --prefix (Join-Path $dailyRoot 'apps/web') *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Web build failed (see output above).' }
        Write-Ok 'web bundle built'
    } else {
        Write-Ok 'web bundle present (use -RebuildWeb after UI code changes)'
    }

    # -- [3/5] chat backend ----------------------------------------------------
    Write-Host '  [3/5] Chat service' -ForegroundColor Cyan
    $dailyListener = Get-NetTCPConnection -LocalPort 9510 -State Listen -ErrorAction SilentlyContinue
    if ($dailyListener) {
        try {
            $dailyStatus = Invoke-RestMethod 'http://127.0.0.1:9510/api/status' -TimeoutSec 5
            if (-not $dailyStatus.daily_services) { throw 'Port 9510 belongs to a non-daily backend. Stop it before starting daily mode.' }
            Write-Ok 'already running'
        } catch { throw $_ }
    } else {
        Write-Step 'starting chat service...'
        Start-Process -FilePath $dailyPython -ArgumentList @('scripts/run_daily.py') -WorkingDirectory $dailyRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $dailyLogs 'daily-bff.log') -RedirectStandardError (Join-Path $dailyLogs 'daily-bff.err.log')
        $dailyReady = $false
        for ($dailyAttempt = 0; $dailyAttempt -lt 60; $dailyAttempt++) {
            try { $dailyStatus = Invoke-RestMethod 'http://127.0.0.1:9510/api/status' -TimeoutSec 2; $dailyReady = [bool]$dailyStatus.daily_services } catch {}
            if ($dailyReady) { break }
            Start-Sleep -Seconds 1
        }
        if (-not $dailyReady) { throw 'Chat service is not ready. Check data/logs/daily-bff.err.log.' }
        Write-Ok 'chat service ready on 127.0.0.1:9510'
    }
} finally {
    $ErrorActionPreference = $dailyEap
}

# -- [4/5] Ollama (local models; cloud works without it) ---------------------
Write-Host '  [4/5] Ollama (local models)' -ForegroundColor Cyan
$ollamaUp = [bool](Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)
if ($ollamaUp) {
    Write-Ok 'running'
} elseif (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Step 'starting ollama serve...'
    Start-Process -FilePath 'ollama' -ArgumentList @('serve') -WindowStyle Hidden
    $dailyDeadline = (Get-Date).AddSeconds(45)
    do { Start-Sleep -Seconds 2; $ollamaUp = [bool](Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue) } until ($ollamaUp -or (Get-Date) -gt $dailyDeadline)
    if ($ollamaUp) { Write-Ok 'started' } else { Write-Warn2 'did not become ready in time; local models stay unavailable until Ollama runs' }
} else {
    Write-Warn2 'not installed; only cloud models are usable'
}

# -- [5/5] open browser --------------------------------------------------------
Write-Host '  [5/5] Open browser' -ForegroundColor Cyan
Start-Process 'http://127.0.0.1:9510'
Write-Ok 'http://127.0.0.1:9510'

Write-Host ''
Write-Host '  Started. Daily Web: http://127.0.0.1:9510' -ForegroundColor Green
Write-Host '  Exit from the page ("退出") or just close the page; all services' -ForegroundColor DarkGray
Write-Host '  (chat, tool containers, stock backend) stop themselves in ~15s.' -ForegroundColor DarkGray
Write-Host ''

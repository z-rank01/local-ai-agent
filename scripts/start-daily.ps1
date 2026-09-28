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

# Use the stock service's configured loopback Ollama endpoint when available.
$dailyOllamaUrl = 'http://127.0.0.1:11434'
$dailyServicesFile = Join-Path $dailyRoot 'data/private/daily-services.json'
if (Test-Path -LiteralPath $dailyServicesFile) {
    $dailyServices = Get-Content -LiteralPath $dailyServicesFile -Raw | ConvertFrom-Json
    if ($dailyServices.root) {
        $dailyStockConfig = Join-Path ([string]$dailyServices.root) 'config/local.json'
        if (Test-Path -LiteralPath $dailyStockConfig) {
            $dailyStockSettings = Get-Content -LiteralPath $dailyStockConfig -Raw | ConvertFrom-Json
            if ($dailyStockSettings.ollama_base_url) { $dailyOllamaUrl = [string]$dailyStockSettings.ollama_base_url }
        }
    }
}
$dailyOllamaUri = $null
if (-not [Uri]::TryCreate($dailyOllamaUrl, [UriKind]::Absolute, [ref]$dailyOllamaUri) -or
        $dailyOllamaUri.Scheme -ne 'http' -or $dailyOllamaUri.Host -ne '127.0.0.1' -or
        $dailyOllamaUri.Port -lt 1024 -or $dailyOllamaUri.Port -gt 65535 -or
        $dailyOllamaUri.AbsolutePath -notin @('', '/')) {
    throw 'Ollama address must be a 127.0.0.1 loopback URL with a valid port.'
}
$dailyOllamaUrl = $dailyOllamaUrl.TrimEnd('/')
$env:OLLAMA_BASE_URL = $dailyOllamaUrl

Write-Host ''
Write-Host '  Local AI Agent - starting' -ForegroundColor White
Write-Host ''

# -- [0/6] workspace ---------------------------------------------------------
$workspace = Join-Path $dailyRoot 'data/workspace'
if (-not (Test-Path -LiteralPath (Join-Path $workspace '.git'))) {
    Write-Host '  [0/6] Workspace' -ForegroundColor Cyan
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

# -- [1/6] Docker + tool containers ------------------------------------------
$dailyEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    if (-not $SkipTools) {
        Write-Host '  [1/6] Docker & tool containers' -ForegroundColor Cyan
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

    # -- [2/6] Web build ------------------------------------------------------
    Write-Host '  [2/6] Web UI' -ForegroundColor Cyan
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

    # -- [3/6] stock backend ---------------------------------------------------
    # Started HERE, as a top-level process, on purpose.  A stock backend spawned by
    # the chat backend cannot write the stock repository on this machine (measured
    # 2026-09-28: PermissionError 13 on simulation/, then SQLite "unable to open
    # database file"), while the same executable started from this launcher writes it
    # fine.  The restriction follows the process tree, so no amount of nesting inside
    # the chat backend escapes it.  Order matters: the backend must be up before the
    # chat backend starts, otherwise the chat backend takes the failing path itself.
    Write-Host '  [3/6] Stock backend' -ForegroundColor Cyan
    $dailyStockSettingsFile = Join-Path $dailyRoot 'data/private/daily-services.json'
    $dailyStock = $null
    if (Test-Path -LiteralPath $dailyStockSettingsFile) {
        $dailyStock = Get-Content -LiteralPath $dailyStockSettingsFile -Raw | ConvertFrom-Json
    }
    if (-not $dailyStock -or -not $dailyStock.enabled -or -not $dailyStock.root) {
        Write-Ok 'not configured or switched off (chat only)'
    } else {
        $dailyStockRoot = [string]$dailyStock.root
        $dailyStockPort = if ($dailyStock.port) { [int]$dailyStock.port } else { 8765 }
        $dailyStockState = Join-Path $dailyStockRoot ([string]$dailyStock.state_dir)
        $dailyStockPython = Join-Path $dailyStockRoot '.venv-paper/Scripts/python.exe'
        $dailyStockScript = Join-Path $dailyStockRoot 'scripts/run_paper.py'

        $dailyStockUp = $false
        try { $null = Invoke-RestMethod "http://127.0.0.1:$dailyStockPort/health" -TimeoutSec 3; $dailyStockUp = $true } catch {}
        if ($dailyStockUp) {
            Write-Ok "already running on 127.0.0.1:$dailyStockPort"
        } elseif (-not (Test-Path -LiteralPath $dailyStockPython) -or -not (Test-Path -LiteralPath $dailyStockScript)) {
            Write-Warn2 "stock python or run_paper.py missing under $dailyStockRoot; start it from the page after fixing the path"
        } elseif (Get-NetTCPConnection -LocalPort $dailyStockPort -State Listen -ErrorAction SilentlyContinue) {
            Write-Warn2 "port $dailyStockPort is taken by something else; not touching it"
        } else {
            Write-Step 'starting stock backend...'
            $dailyStockProc = Start-Process -FilePath $dailyStockPython `
                -ArgumentList @('-X', 'utf8', $dailyStockScript, '--state-dir', $dailyStockState, '--port', "$dailyStockPort", '--no-worker') `
                -WorkingDirectory $dailyStockRoot -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $dailyLogs 'stock-service.log') `
                -RedirectStandardError (Join-Path $dailyLogs 'stock-service.err.log') -PassThru
            if ($dailyStockProc) { $env:STOCK_LAUNCHER_PID = "$($dailyStockProc.Id)" }
            $dailyStockDeadline = (Get-Date).AddSeconds(45)
            do {
                Start-Sleep -Milliseconds 750
                try { $null = Invoke-RestMethod "http://127.0.0.1:$dailyStockPort/health" -TimeoutSec 3; $dailyStockUp = $true } catch {}
            } until ($dailyStockUp -or (Get-Date) -gt $dailyStockDeadline)
            if ($dailyStockUp) {
                Write-Ok "started on 127.0.0.1:$dailyStockPort"
                if ($dailyStock.worker) {
                    try {
                        $dailyToken = (Get-Content -LiteralPath (Join-Path $dailyStockState '.paper-control-token') -Raw).Trim()
                        $null = Invoke-RestMethod "http://127.0.0.1:$dailyStockPort/api/control/action" -Method Post `
                            -Headers @{ Authorization = "Bearer $dailyToken" } -ContentType 'application/json' `
                            -Body '{"action":"worker_start"}' -TimeoutSec 10
                        Write-Ok 'task execution resumed'
                    } catch { Write-Warn2 "backend is up but worker_start failed: $($_.Exception.Message)" }
                }
            } else {
                Write-Warn2 'did not become ready; see data/logs/stock-service.err.log'
            }
        }
    }

    # -- [4/6] chat backend ----------------------------------------------------
    Write-Host '  [4/6] Chat service' -ForegroundColor Cyan
    $dailyReady = $false
    try {
        $dailyStatus = Invoke-RestMethod 'http://127.0.0.1:9510/api/status' -TimeoutSec 5
        $dailyReady = [bool]$dailyStatus.daily_services
    } catch {}
    if ($dailyReady) {
        Write-Ok 'already running'
    } else {
        if (Get-NetTCPConnection -LocalPort 9510 -State Listen -ErrorAction SilentlyContinue) {
            throw 'Port 9510 belongs to a non-daily backend. Stop it before starting daily mode.'
        }
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

# -- [5/6] Ollama (local models; cloud works without it) ---------------------
Write-Host '  [5/6] Ollama (local models)' -ForegroundColor Cyan
$ollamaUp = $false
try { $ollamaUp = [bool](Invoke-RestMethod ($dailyOllamaUrl + '/api/tags') -TimeoutSec 4) } catch {}
if ($ollamaUp) {
    Write-Ok 'running'
} elseif (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Step 'starting ollama serve...'
    $dailyOldOllamaHost = $env:OLLAMA_HOST
    try {
        $env:OLLAMA_HOST = '127.0.0.1:' + $dailyOllamaUri.Port
        Start-Process -FilePath 'ollama' -ArgumentList @('serve') -WindowStyle Hidden
    } finally {
        if ($null -eq $dailyOldOllamaHost) { Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue }
        else { $env:OLLAMA_HOST = $dailyOldOllamaHost }
    }
    $dailyDeadline = (Get-Date).AddSeconds(45)
    do {
        Start-Sleep -Seconds 2
        try { $ollamaUp = [bool](Invoke-RestMethod ($dailyOllamaUrl + '/api/tags') -TimeoutSec 4) } catch { $ollamaUp = $false }
    } until ($ollamaUp -or (Get-Date) -gt $dailyDeadline)
    if ($ollamaUp) { Write-Ok 'started' } else { Write-Warn2 'did not become ready in time; local models stay unavailable until Ollama runs' }
} else {
    Write-Warn2 'not installed; only cloud models are usable'
}

# -- [6/6] open browser --------------------------------------------------------
Write-Host '  [6/6] Open browser' -ForegroundColor Cyan
try {
    Start-Process 'http://127.0.0.1:9510'
    Write-Ok 'http://127.0.0.1:9510'
} catch {
    Write-Warn2 'Browser could not open automatically; use http://127.0.0.1:9510'
}

Write-Host ''
Write-Host '  Started. Daily Web: http://127.0.0.1:9510' -ForegroundColor Green
Write-Host '  Exit from the page ("退出") or just close the page; all services' -ForegroundColor DarkGray
Write-Host '  (chat, tool containers, stock backend) stop themselves in ~15s.' -ForegroundColor DarkGray
Write-Host ''

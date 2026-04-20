#Requires -Version 5.1
<#
.SYNOPSIS
    One-click startup: Local AI Agent TUI + Docker skill services.
.DESCRIPTION
    1. Starts Docker Desktop if it is not already running
    2. Checks Ollama and starts it if not running
    3. Asks whether to enable web search
    4. Brings up Docker skill containers (skill-files, skill-runner, optionally websearch)
    5. Waits for skill services to become healthy
    6. Launches TUI (python -m tui)
.PARAMETER TimeoutSec
    Max seconds to wait for skill services to become healthy. Default: 120
.PARAMETER Build
    If set, forces a rebuild of Docker images (docker compose up --build).
.PARAMETER SkipTUI
    If set, only starts Docker services without launching TUI.
#>

param(
    [int]   $TimeoutSec = 120,
    [switch]$Build,
    [switch]$SkipTUI
)

$ErrorActionPreference = "Stop"

$scriptRoot  = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot

# ── Helpers ───────────────────────────────────────────────────────────────

function Write-Step  ([string]$msg) { Write-Host "`n:: $msg" -ForegroundColor Cyan }
function Write-Ok    ([string]$msg) { Write-Host "   [OK] $msg" -ForegroundColor Green }
function Write-Wait  ([string]$msg) { Write-Host "   ... $msg" -ForegroundColor DarkGray }
function Write-Fail  ([string]$msg) { Write-Host "   [FAIL] $msg" -ForegroundColor Red }

function Wait-Until {
    param(
        [scriptblock]$Condition,
        [int]$Timeout,
        [string]$Label,
        [int]$Interval = 3
    )
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $Timeout) {
        if (& $Condition) { return $true }
        $elapsed = [math]::Floor($sw.Elapsed.TotalSeconds)
        Write-Wait "$Label (${elapsed}s / ${Timeout}s)"
        Start-Sleep -Seconds $Interval
    }
    return $false
}

# ══════════════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Local AI Agent v2.0 - Quick Start" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# ── 0. Ensure data/ directory exists ──────────────────────────────────────
$dataDir = Join-Path $projectRoot "data\workspace"
if (-not (Test-Path $dataDir)) {
    Write-Step "Initializing data directory (first run)"
    & (Join-Path $scriptRoot "init-workspace.ps1")
}

# ── 1. Docker Desktop ────────────────────────────────────────────────────
Write-Step "Checking Docker Desktop"

$dockerOk = $false
try { docker info 2>&1 | Out-Null; $dockerOk = ($LASTEXITCODE -eq 0) } catch {}

if ($dockerOk) {
    Write-Ok "Docker daemon already running"
} else {
    $ddPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $ddPath)) {
        Write-Fail "Docker Desktop not found at $ddPath"
        exit 1
    }
    Write-Wait "Starting Docker Desktop ..."
    Start-Process $ddPath

    $ready = Wait-Until -Condition {
        try { docker info 2>&1 | Out-Null; $LASTEXITCODE -eq 0 } catch { $false }
    } -Timeout 120 -Label "Waiting for Docker daemon"

    if (-not $ready) {
        Write-Fail "Docker daemon did not start within 120 seconds"
        exit 1
    }
    Write-Ok "Docker daemon is ready"
}

# ── 2. Ollama ─────────────────────────────────────────────────────────────
Write-Step "Checking Ollama"

$ollamaUrl = "http://localhost:11434"
$ollamaRunning = $false
try {
    $r = Invoke-WebRequest -Uri $ollamaUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    if ($r.StatusCode -eq 200) { $ollamaRunning = $true }
} catch {}

if ($ollamaRunning) {
    Write-Ok "Ollama already running at $ollamaUrl"
} else {
    # Try to locate ollama executable
    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCmd) {
        $ollamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
        if (Test-Path $ollamaExe) {
            $ollamaCmd = Get-Item $ollamaExe
        }
    }

    if (-not $ollamaCmd) {
        Write-Fail "Ollama not found. Install from https://ollama.com/download"
        exit 1
    }

    Write-Wait "Starting Ollama serve ..."
    $ollamaPath = if ($ollamaCmd.Source) { $ollamaCmd.Source } else { $ollamaCmd.FullName }
    Start-Process -FilePath $ollamaPath -ArgumentList "serve" -WindowStyle Hidden

    $ready = Wait-Until -Condition {
        try {
            $r = Invoke-WebRequest -Uri $ollamaUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            $r.StatusCode -eq 200
        } catch { $false }
    } -Timeout 60 -Label "Waiting for Ollama"

    if (-not $ready) {
        Write-Fail "Ollama did not start within 60 seconds"
        exit 1
    }
    Write-Ok "Ollama is ready"
}

# Check configured model availability
$ollamaModel = "unknown"
try {
    $envFile = Join-Path $projectRoot ".env"
    $match = Select-String -Path $envFile -Pattern 'OLLAMA_MODEL=(.+)' -ErrorAction SilentlyContinue
    if ($match) { $ollamaModel = $match.Matches.Groups[1].Value.Trim() }
} catch {}

if ($ollamaModel -ne "unknown") {
    $modelFound = $false
    try {
        $modelList = ollama list 2>&1
        if ($modelList -match [regex]::Escape($ollamaModel)) { $modelFound = $true }
    } catch {}

    if ($modelFound) {
        Write-Ok "Model '$ollamaModel' is available"
    } else {
        Write-Host "   [WARN] Model '$ollamaModel' not found locally. You may need to run: ollama pull $ollamaModel" -ForegroundColor Yellow
    }
}

# ── 3. Python & Textual check ────────────────────────────────────────────
Write-Step "Checking Python environment"

# Try to activate conda if python is not usable (e.g. Windows Store stub)
$pythonUsable = $false
try {
    $testVer = python -c "import sys; print(sys.version)" 2>&1
    if ($LASTEXITCODE -eq 0 -and $testVer -match '\d+\.\d+') { $pythonUsable = $true }
} catch {}

if (-not $pythonUsable) {
    # Auto-detect and activate conda
    $condaHooks = @(
        "$env:USERPROFILE\miniconda3\shell\condabin\conda-hook.ps1",
        "$env:USERPROFILE\anaconda3\shell\condabin\conda-hook.ps1",
        "C:\ProgramData\miniconda3\shell\condabin\conda-hook.ps1",
        "C:\ProgramData\anaconda3\shell\condabin\conda-hook.ps1"
    )
    foreach ($hook in $condaHooks) {
        if (Test-Path $hook) {
            Write-Wait "Activating conda from $hook"
            . $hook
            conda activate base
            break
        }
    }
}

$pythonOk = $false
try {
    $pyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>&1
    if ($LASTEXITCODE -eq 0 -and $pyVer -match '^\d+\.\d+') {
        Write-Ok "Python: $pyVer"
        $pythonOk = $true
    } else {
        throw "not usable"
    }
} catch {
    Write-Fail "Python not found. Install Python 3.11+ from https://python.org"
    exit 1
}

# Check if textual is installed
try {
    $textualVer = python -c "import textual; print(textual.__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Textual: v$textualVer"
    } else {
        Write-Host "   [WARN] Textual not installed. Run: pip install -r requirements.txt" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   [WARN] Textual not installed. Run: pip install -r requirements.txt" -ForegroundColor Yellow
}

# ── 4. Web Search option ──────────────────────────────────────────────────
Write-Step "Web Search (SearXNG)"

$enableWebSearch = $false
$choice = Read-Host "   是否启用联网搜索功能? (Y/N, 默认 N)"
if ($choice -match '^[Yy]') {
    $enableWebSearch = $true
    Write-Ok "Web Search 已启用"
} else {
    Write-Ok "Web Search 已跳过 (可稍后在 .env 中设置 ENABLE_WEBSEARCH=true)"
}

# Update .env ENABLE_WEBSEARCH value
$envFile = Join-Path $projectRoot ".env"
if (Test-Path $envFile) {
    $envContent = Get-Content $envFile -Raw
    if ($enableWebSearch) {
        $envContent = $envContent -replace 'ENABLE_WEBSEARCH=\w+', 'ENABLE_WEBSEARCH=true'
    } else {
        $envContent = $envContent -replace 'ENABLE_WEBSEARCH=\w+', 'ENABLE_WEBSEARCH=false'
    }
    Set-Content -Path $envFile -Value $envContent -NoNewline
}

# ── 5. Docker Compose (skill services only) ──────────────────────────────
Write-Step "Starting skill service containers"

Push-Location $projectRoot
try {
    $composeArgs = @("compose")
    if ($enableWebSearch) { $composeArgs += @("--profile", "websearch") }
    $composeArgs += @("up", "-d", "--remove-orphans")
    if ($Build) { $composeArgs += "--build" }

    # docker writes progress to stderr; merge streams without treating as fatal
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & docker @composeArgs 2>&1 | ForEach-Object { Write-Host "   $_" }
    } finally {
        $ErrorActionPreference = $prevEAP
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "docker compose up failed (exit code $LASTEXITCODE)"
        exit 1
    }
} finally {
    Pop-Location
}
Write-Ok "Containers are up"

# ── 6. Wait for skill services to become healthy ─────────────────────────
Write-Step "Waiting for skill services to become healthy"

$sfPort = "9101"
$srPort = "9102"
try {
    $envFile = Join-Path $projectRoot ".env"
    $m1 = Select-String -Path $envFile -Pattern 'SKILL_FILES_PORT=(\d+)' -ErrorAction SilentlyContinue
    if ($m1) { $sfPort = $m1.Matches.Groups[1].Value }
    $m2 = Select-String -Path $envFile -Pattern 'SKILL_RUNNER_PORT=(\d+)' -ErrorAction SilentlyContinue
    if ($m2) { $srPort = $m2.Matches.Groups[1].Value }
} catch {}

$ready = Wait-Until -Condition {
    try {
        $r1 = Invoke-WebRequest -Uri "http://localhost:${sfPort}/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        $r2 = Invoke-WebRequest -Uri "http://localhost:${srPort}/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        ($r1.StatusCode -eq 200) -and ($r2.StatusCode -eq 200)
    } catch { $false }
} -Timeout $TimeoutSec -Label "Polling skill-files and skill-runner health"

if (-not $ready) {
    Write-Fail "Skill services did not become healthy within ${TimeoutSec}s"
    Write-Host "   Check: docker compose ps / docker compose logs" -ForegroundColor Yellow
    exit 1
}
Write-Ok "All skill services are healthy"

# ── 7. Launch TUI ─────────────────────────────────────────────────────────
if ($SkipTUI) {
    Write-Step "Skipping TUI launch (-SkipTUI)"
} else {
    Write-Step "Launching TUI"
    Write-Ok "Starting python -m tui ..."
}

# ── Summary ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  All services started successfully!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  skill-files   :  http://localhost:$sfPort" -ForegroundColor White
Write-Host "  skill-runner  :  http://localhost:$srPort" -ForegroundColor White
if ($enableWebSearch) {
    $swPort = "9103"
    try {
        $m3 = Select-String -Path $envFile -Pattern 'SKILL_WEBSEARCH_PORT=(\d+)' -ErrorAction SilentlyContinue
        if ($m3) { $swPort = $m3.Matches.Groups[1].Value }
    } catch {}
    Write-Host "  skill-websearch: http://localhost:$swPort" -ForegroundColor White
}
Write-Host "  Ollama        :  http://localhost:11434" -ForegroundColor White
Write-Host ""
Write-Host "  Stop services: docker compose down" -ForegroundColor DarkGray
Write-Host ""

# Launch TUI in current console
if (-not $SkipTUI) {
    Push-Location $projectRoot
    python -m tui
    Pop-Location
}

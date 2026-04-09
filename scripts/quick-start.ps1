#Requires -Version 5.1
<#
.SYNOPSIS
    One-click startup: Open WebUI + local-ai-agent Docker services.
.DESCRIPTION
    1. Starts Docker Desktop if it is not already running
    2. Checks Ollama and starts it if not running
    3. Launches "open-webui serve" in a new terminal window (via conda)
    4. Waits for the Docker daemon, then brings up local-ai-agent containers
    5. Polls Open WebUI until it responds, then opens the default browser
.PARAMETER CondaRoot
    Path to the conda installation directory. Default: C:\ProgramData\miniconda3
.PARAMETER CondaEnv
    Name of the conda environment with open-webui. Default: open-webui
.PARAMETER OpenWebuiPort
    Port that open-webui listens on. Default: 8080
.PARAMETER TimeoutSec
    Max seconds to wait for Open WebUI to become responsive. Default: 180
.PARAMETER Build
    If set, forces a rebuild of Docker images (docker compose up --build).
#>

param(
    [string]$CondaRoot     = "C:\ProgramData\miniconda3",
    [string]$CondaEnv      = "open-webui",
    [int]   $OpenWebuiPort = 8080,
    [int]   $TimeoutSec    = 180,
    [switch]$Build
)

$ErrorActionPreference = "Stop"

$scriptRoot   = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot  = Split-Path -Parent $scriptRoot
$openWebuiUrl = "http://localhost:$OpenWebuiPort"

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
Write-Host "  Local AI Agent - Quick Start" -ForegroundColor Cyan
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

# ── 3. Open WebUI (conda) ────────────────────────────────────────────────
Write-Step "Launching Open WebUI (conda env: $CondaEnv)"

$alreadyRunning = $false
try {
    $resp = Invoke-WebRequest -Uri $openWebuiUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    if ($resp.StatusCode -eq 200) { $alreadyRunning = $true }
} catch {}

if ($alreadyRunning) {
    Write-Ok "Open WebUI already running at $openWebuiUrl"
} else {
    # Anaconda Prompt = cmd.exe /K activate.bat — avoids PowerShell execution-policy issues
    $activateBat = Join-Path $CondaRoot "Scripts\activate.bat"
    if (-not (Test-Path $activateBat)) {
        Write-Fail "Conda activate.bat not found: $activateBat"
        Write-Host "   Set -CondaRoot to your conda installation path." -ForegroundColor Yellow
        exit 1
    }

    # Launch open-webui serve in a new cmd.exe window (same as Anaconda Prompt)
    $cmdArgs = "/K `"title Open WebUI Server && `"$activateBat`" $CondaRoot && conda activate $CondaEnv && echo. && echo Starting open-webui serve ... && echo. && open-webui serve`""
    Start-Process cmd.exe -ArgumentList $cmdArgs
    Write-Ok "Open WebUI launched in new Anaconda Prompt window"
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

# ── 5. Docker Compose ────────────────────────────────────────────────────
Write-Step "Starting local-ai-agent containers"

Push-Location $projectRoot
try {
    $composeArgs = @("compose")
    if ($enableWebSearch) { $composeArgs += @("--profile", "websearch") }
    $composeArgs += @("up", "-d")
    if ($Build) { $composeArgs += "--build" }

    & docker @composeArgs 2>&1 | ForEach-Object { Write-Host "   $_" }

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "docker compose up failed (exit code $LASTEXITCODE)"
        exit 1
    }
} finally {
    Pop-Location
}
Write-Ok "Containers are up"

# ── 6. Wait for Open WebUI ───────────────────────────────────────────────
if (-not $alreadyRunning) {
    Write-Step "Waiting for Open WebUI to become ready"

    $ready = Wait-Until -Condition {
        try {
            $r = Invoke-WebRequest -Uri $openWebuiUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            $r.StatusCode -eq 200
        } catch { $false }
    } -Timeout $TimeoutSec -Label "Polling $openWebuiUrl"

    if (-not $ready) {
        Write-Fail "Open WebUI did not respond within ${TimeoutSec}s"
        Write-Host "   It may still be loading. Try opening $openWebuiUrl manually." -ForegroundColor Yellow
        exit 1
    }
    Write-Ok "Open WebUI is ready"
}

# ── 7. Open browser ──────────────────────────────────────────────────────
Write-Step "Opening browser"
Start-Process $openWebuiUrl
Write-Ok "Launched $openWebuiUrl in default browser"

# ── 8. Tailscale remote access check ─────────────────────────────────────
Write-Step "Checking Tailscale (remote access)"

$tailscaleIp = $null
$tailscaleCmd = Get-Command tailscale -ErrorAction SilentlyContinue
if ($tailscaleCmd) {
    try {
        $tsStatus = tailscale status --json 2>$null | ConvertFrom-Json
        if ($tsStatus.Self.Online -eq $true) {
            $tailscaleIp = ($tsStatus.Self.TailscaleIPs | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' } | Select-Object -First 1)
            Write-Ok "Tailscale connected (IP: $tailscaleIp)"
        } else {
            Write-Host "   [WARN] Tailscale installed but not connected. Run 'tailscale up' to enable remote access." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "   [WARN] Tailscale installed but status check failed." -ForegroundColor Yellow
    }
} else {
    Write-Host "   [INFO] Tailscale not installed. Install from https://tailscale.com/download for remote access." -ForegroundColor DarkGray
}

# ── Summary ───────────────────────────────────────────────────────────────
$gwPort = "8400"
try {
    $envFile = Join-Path $projectRoot ".env"
    $match = Select-String -Path $envFile -Pattern 'GATEWAY_PORT=(\d+)' -ErrorAction SilentlyContinue
    if ($match) { $gwPort = $match.Matches.Groups[1].Value }
} catch {}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  All services started successfully!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Open WebUI (local)  :  $openWebuiUrl" -ForegroundColor White
Write-Host "  AI Gateway          :  http://localhost:$gwPort" -ForegroundColor White
if ($tailscaleIp) {
    Write-Host "  Open WebUI (remote) :  http://${tailscaleIp}:$OpenWebuiPort" -ForegroundColor Cyan
}
if ($enableWebSearch) {
    Write-Host "  Web Search          :  Enabled (SearXNG)" -ForegroundColor White
}
Write-Host ""
if ($tailscaleIp) {
    Write-Host "  Remote: Use http://${tailscaleIp}:$OpenWebuiPort from any Tailscale device" -ForegroundColor Cyan
} else {
    Write-Host "  Remote: Install Tailscale for remote access from phone/other computers" -ForegroundColor DarkGray
}
Write-Host "  Tip: To stop everything, close the Open WebUI window" -ForegroundColor DarkGray
Write-Host "       and run:  docker compose down  in the project root." -ForegroundColor DarkGray
Write-Host ""

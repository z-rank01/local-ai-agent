#Requires -Version 5.1
<#
.SYNOPSIS
    One-click startup: Local AI Agent Ink CLI + Python BFF + Docker skill services.
.DESCRIPTION
    1. Starts Docker Desktop if it is not already running
    2. Checks Ollama and starts it if not running
    3. Checks Python, BFF dependencies, Node.js, and npm
    4. Asks whether to enable web search
    5. Brings up Docker skill containers (skill-files, skill-runner, optionally websearch)
    6. Waits for skill services to become healthy
    7. Starts the Python BFF adapter if needed
    8. Installs Ink frontend dependencies if missing
    9. Launches the Ink CLI frontend
.PARAMETER TimeoutSec
    Max seconds to wait for skill services and BFF to become healthy. Default: 120
.PARAMETER Build
    If set, forces a rebuild of Docker images (docker compose up --build).
.PARAMETER SkipCLI
    If set, starts backend services and the Python BFF but does not launch the Ink CLI.
    The old -SkipTUI and -SkipUI flags are still accepted for compatibility.
#>

param(
    [int]$TimeoutSec = 120,
    [switch]$Build,
    [Alias("SkipTUI", "SkipUI")]
    [switch]$SkipCLI
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
$envFile = Join-Path $projectRoot ".env"
$bffProcess = $null
$startedBff = $false

function Write-Step([string]$msg) { Write-Host "`n:: $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host "   [OK] $msg" -ForegroundColor Green }
function Write-Wait([string]$msg) { Write-Host "   ... $msg" -ForegroundColor DarkGray }
function Write-Fail([string]$msg) { Write-Host "   [FAIL] $msg" -ForegroundColor Red }

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

function Get-FirstCommandPath {
    param(
        [string]$CommandName,
        [string[]]$Candidates = @()
    )

    $cmd = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($cmd) {
        if ($cmd.Source) { return $cmd.Source }
        if ($cmd.Path) { return $cmd.Path }
        if ($cmd.Definition) { return $cmd.Definition }
    }

    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    return $null
}

function Ensure-PathContains {
    param([string]$Dir)
    if (-not $Dir) { return }
    $parts = $env:Path -split ";"
    if ($parts -notcontains $Dir) {
        $env:Path = "$Dir;$env:Path"
    }
}

function Stop-BffProcess {
    param([System.Diagnostics.Process]$Process)
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Local AI Agent v2.1 - Quick Start" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 0. Ensure workspace data exists
$dataDir = Join-Path $projectRoot "data\workspace"
if (-not (Test-Path $dataDir)) {
    Write-Step "Initializing workspace data directory"
    & (Join-Path $scriptRoot "init-workspace.ps1")
}

# 1. Docker Desktop
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

    Write-Wait "Starting Docker Desktop"
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

# 2. Ollama
Write-Step "Checking Ollama"

$ollamaUrl = "http://localhost:11434"
$ollamaRunning = $false
try {
    $response = Invoke-WebRequest -Uri $ollamaUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    if ($response.StatusCode -eq 200) { $ollamaRunning = $true }
} catch {}

if ($ollamaRunning) {
    Write-Ok "Ollama already running at $ollamaUrl"
} else {
    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCmd) {
        $ollamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
        if (Test-Path $ollamaExe) {
            $ollamaCmd = Get-Item $ollamaExe
        }
    }

    if (-not $ollamaCmd) {
        Write-Fail "Ollama not found. Install it from https://ollama.com/download"
        exit 1
    }

    $ollamaPath = if ($ollamaCmd.Source) { $ollamaCmd.Source } else { $ollamaCmd.FullName }
    Write-Wait "Starting Ollama serve"
    Start-Process -FilePath $ollamaPath -ArgumentList "serve" -WindowStyle Hidden

    $ready = Wait-Until -Condition {
        try {
            $result = Invoke-WebRequest -Uri $ollamaUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            $result.StatusCode -eq 200
        } catch { $false }
    } -Timeout 60 -Label "Waiting for Ollama"

    if (-not $ready) {
        Write-Fail "Ollama did not start within 60 seconds"
        exit 1
    }

    Write-Ok "Ollama is ready"
}

$ollamaModel = Get-EnvValue -FilePath $envFile -Key "OLLAMA_MODEL" -Default ""
if ($ollamaModel) {
    $modelFound = $false
    try {
        $modelList = ollama list 2>&1
        if ($modelList -match [regex]::Escape($ollamaModel)) { $modelFound = $true }
    } catch {}

    if ($modelFound) {
        Write-Ok "Model '$ollamaModel' is available"
    } else {
        Write-Host "   [WARN] Model '$ollamaModel' not found locally. You may need: ollama pull $ollamaModel" -ForegroundColor Yellow
    }
}

# 3. Python and frontend runtime dependencies
Write-Step "Checking Python environment"

$pythonUsable = $false
try {
    $testVer = python -c "import sys; print(sys.version)" 2>&1
    if ($LASTEXITCODE -eq 0 -and $testVer -match "\d+\.\d+") { $pythonUsable = $true }
} catch {}

if (-not $pythonUsable) {
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

$pythonExe = Get-FirstCommandPath -CommandName "python"
if (-not $pythonExe) {
    Write-Fail "Python not found. Install Python 3.11+ or fix your environment"
    exit 1
}

$pyVer = & $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>&1
if ($LASTEXITCODE -ne 0 -or -not $pyVer) {
    Write-Fail "Python is not usable in the current shell"
    exit 1
}
Write-Ok "Python: $pyVer"

$bffDeps = & $pythonExe -c "import fastapi, uvicorn; print(f'fastapi {fastapi.__version__}, uvicorn {uvicorn.__version__}')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Required BFF packages are missing. Run: python -m pip install -r requirements.txt"
    exit 1
}
Write-Ok "BFF deps: $bffDeps"

Write-Step "Checking Node.js environment"

$nodeExe = Get-FirstCommandPath -CommandName "node" -Candidates @(
    "C:\Program Files\nodejs\node.exe",
    "$env:LOCALAPPDATA\Programs\nodejs\node.exe"
)
$npmCmd = Get-FirstCommandPath -CommandName "npm.cmd" -Candidates @(
    "C:\Program Files\nodejs\npm.cmd",
    "$env:LOCALAPPDATA\Programs\nodejs\npm.cmd"
)

if (-not $nodeExe -or -not $npmCmd) {
    Write-Fail "Node.js or npm not found. Install Node.js LTS on the host system"
    exit 1
}

$nodeBin = Split-Path -Parent $nodeExe
Ensure-PathContains -Dir $nodeBin

$nodeVer = & $nodeExe -p "process.version" 2>&1
if ($LASTEXITCODE -ne 0 -or -not $nodeVer) {
    Write-Fail "Node.js executable exists but is not runnable: $nodeExe"
    exit 1
}
Write-Ok "Node.js: $nodeVer"

$npmVer = & $npmCmd --version 2>&1
if ($LASTEXITCODE -ne 0 -or -not $npmVer) {
    Write-Fail "npm executable exists but is not runnable: $npmCmd"
    exit 1
}
Write-Ok "npm: $npmVer"

# 4. Web Search option
Write-Step "Web search (SearXNG)"

$enableWebSearch = $false
$choice = Read-Host "   Enable web search? (Y/N, default N)"
if ($choice -match "^[Yy]") {
    $enableWebSearch = $true
    Write-Ok "Web search enabled"
} else {
    Write-Ok "Web search disabled"
}

if (Test-Path $envFile) {
    $envContent = Get-Content $envFile -Raw
    if ($enableWebSearch) {
        $envContent = $envContent -replace "ENABLE_WEBSEARCH=\w+", "ENABLE_WEBSEARCH=true"
    } else {
        $envContent = $envContent -replace "ENABLE_WEBSEARCH=\w+", "ENABLE_WEBSEARCH=false"
    }
    Set-Content -Path $envFile -Value $envContent -NoNewline
}

# 5. Docker Compose
Write-Step "Starting skill service containers"

Push-Location $projectRoot
try {
    $composeArgs = @("compose")
    if ($enableWebSearch) { $composeArgs += @("--profile", "websearch") }
    $composeArgs += @("up", "-d", "--remove-orphans")
    if ($Build) { $composeArgs += "--build" }

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

# 6. Wait for skills
Write-Step "Waiting for skill services to become healthy"

$sfPort = Get-EnvValue -FilePath $envFile -Key "SKILL_FILES_PORT" -Default "9101"
$srPort = Get-EnvValue -FilePath $envFile -Key "SKILL_RUNNER_PORT" -Default "9102"

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

# 7. Start BFF if needed
Write-Step "Checking Python frontend adapter (BFF)"

$bffHost = Get-EnvValue -FilePath $envFile -Key "BFF_HOST" -Default "127.0.0.1"
$bffPort = Get-EnvValue -FilePath $envFile -Key "BFF_PORT" -Default "9510"
$bffHealthHost = if ($bffHost -eq "0.0.0.0") { "127.0.0.1" } else { $bffHost }
$bffUrl = "http://${bffHealthHost}:${bffPort}"
$bffHealthUrl = "$bffUrl/health"

$bffHealthy = $false
try {
    $health = Invoke-WebRequest -Uri $bffHealthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    if ($health.StatusCode -eq 200) { $bffHealthy = $true }
} catch {}

if ($bffHealthy) {
    Write-Ok "BFF already running at $bffUrl"
} else {
    Write-Wait "Starting Python BFF"
    $bffProcess = Start-Process -FilePath $pythonExe -ArgumentList @("-m", "bff") -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    $startedBff = $true

    $ready = Wait-Until -Condition {
        try {
            $health = Invoke-WebRequest -Uri $bffHealthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            $health.StatusCode -eq 200
        } catch { $false }
    } -Timeout 30 -Label "Waiting for BFF health"

    if (-not $ready) {
        Stop-BffProcess -Process $bffProcess
        Write-Fail "BFF did not become healthy within 30 seconds"
        exit 1
    }

    Write-Ok "BFF is ready at $bffUrl"
}

# 8. Ensure Ink frontend dependencies
$inkDir = Join-Path $projectRoot "apps\cli-ink"
$inkNodeModules = Join-Path $inkDir "node_modules\ink"

Write-Step "Checking Ink CLI dependencies"
if (-not (Test-Path $inkDir)) {
    if ($startedBff) { Stop-BffProcess -Process $bffProcess }
    Write-Fail "Ink frontend directory not found: $inkDir"
    exit 1
}

if (-not (Test-Path $inkNodeModules)) {
    Write-Wait "Installing Ink frontend dependencies"
    Push-Location $inkDir
    try {
        & $npmCmd install 2>&1 | ForEach-Object { Write-Host "   $_" }
        if ($LASTEXITCODE -ne 0) {
            if ($startedBff) { Stop-BffProcess -Process $bffProcess }
            Write-Fail "npm install failed (exit code $LASTEXITCODE)"
            exit 1
        }
    } finally {
        Pop-Location
    }
    Write-Ok "Ink frontend dependencies installed"
} else {
    Write-Ok "Ink frontend dependencies already installed"
}

# Summary
$swPort = Get-EnvValue -FilePath $envFile -Key "SKILL_WEBSEARCH_PORT" -Default "9103"
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  All services started successfully!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  skill-files   :  http://localhost:$sfPort" -ForegroundColor White
Write-Host "  skill-runner  :  http://localhost:$srPort" -ForegroundColor White
if ($enableWebSearch) {
    Write-Host "  skill-websearch: http://localhost:$swPort" -ForegroundColor White
}
Write-Host "  Ollama        :  http://localhost:11434" -ForegroundColor White
Write-Host "  BFF           :  $bffUrl" -ForegroundColor White
Write-Host ""
Write-Host "  Stop services: docker compose down" -ForegroundColor DarkGray
Write-Host ""

# 9. Launch Ink CLI
if ($SkipCLI) {
    Write-Step "Skipping Ink CLI launch (-SkipCLI)"
} else {
    Write-Step "Launching Ink CLI"
    $env:LOCAL_AI_AGENT_API_URL = $bffUrl
    Push-Location $inkDir
    try {
        & $npmCmd run dev
        $cliExitCode = $LASTEXITCODE
    } finally {
        Pop-Location
        if ($startedBff) {
            Stop-BffProcess -Process $bffProcess
        }
    }

    if ($cliExitCode -ne 0) {
        Write-Fail "Ink CLI exited with code $cliExitCode"
        exit $cliExitCode
    }
}
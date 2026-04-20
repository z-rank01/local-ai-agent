#Requires -Version 5.1
<#
.SYNOPSIS
    Check that all required tools are installed for local-ai-agent.
.DESCRIPTION
    Verifies Docker, Docker Compose, Git, Ollama, Python, and Textual are reachable.
    Exits 0 if all pass, 1 if any fail.
#>

$allOk = $true

function Test-Tool {
    param(
        [string]$Label,
        [string]$Command,
        [string[]]$Arguments
    )
    try {
        $output = (& $Command @Arguments 2>&1 | Select-Object -First 1 | Out-String).Trim()
        Write-Host ("  [PASS] {0,-18} {1}" -f "${Label}:", $output) -ForegroundColor Green
        return $true
    } catch {
        Write-Host ("  [FAIL] {0,-18} not found or error" -f "${Label}:") -ForegroundColor Red
        return $false
    }
}

Write-Host ""
Write-Host "=== Environment Check ===" -ForegroundColor Cyan

$results = @(
    (Test-Tool -Label "Docker"         -Command "docker" -Arguments @("--version")),
    (Test-Tool -Label "Docker Compose" -Command "docker" -Arguments @("compose", "version")),
    (Test-Tool -Label "Git"            -Command "git"    -Arguments @("--version")),
    (Test-Tool -Label "Ollama"         -Command "ollama" -Arguments @("list")),
    (Test-Tool -Label "Python"         -Command "python" -Arguments @("--version"))
)

# Textual check (non-fatal warning)
Write-Host ""
try {
    $textualVer = python -c "import textual; print(textual.__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("  [PASS] {0,-18} {1}" -f "Textual:", "v$($textualVer.Trim())") -ForegroundColor Green
    } else {
        Write-Host ("  [WARN] {0,-18} not installed (run: pip install -r requirements.txt)" -f "Textual:") -ForegroundColor Yellow
    }
} catch {
    Write-Host ("  [WARN] {0,-18} not installed (run: pip install -r requirements.txt)" -f "Textual:") -ForegroundColor Yellow
}

# httpx check (non-fatal warning)
try {
    $httpxVer = python -c "import httpx; print(httpx.__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("  [PASS] {0,-18} {1}" -f "httpx:", "v$($httpxVer.Trim())") -ForegroundColor Green
    } else {
        Write-Host ("  [WARN] {0,-18} not installed (run: pip install -r requirements.txt)" -f "httpx:") -ForegroundColor Yellow
    }
} catch {
    Write-Host ("  [WARN] {0,-18} not installed (run: pip install -r requirements.txt)" -f "httpx:") -ForegroundColor Yellow
}

Write-Host ""
if ($results -contains $false) {
    Write-Host "One or more checks FAILED. Install the missing tools and retry." -ForegroundColor Red
    exit 1
}

Write-Host "All checks passed." -ForegroundColor Green
exit 0

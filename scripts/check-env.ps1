#Requires -Version 5.1
<#
.SYNOPSIS
    Check that all required tools are installed for local-ai-agent.
.DESCRIPTION
    Verifies Docker, Docker Compose, Git, and Ollama are reachable.
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
    (Test-Tool -Label "Ollama"         -Command "ollama" -Arguments @("list"))
)

Write-Host ""
if ($results -contains $false) {
    Write-Host "One or more checks FAILED. Install the missing tools and retry." -ForegroundColor Red
    exit 1
}

Write-Host "All checks passed." -ForegroundColor Green
exit 0

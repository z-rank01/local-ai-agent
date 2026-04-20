#Requires -Version 5.1
<#
.SYNOPSIS
    Initialize data/ directory structure and workspace Git repository.
.DESCRIPTION
    Creates the standard directory layout (data/logs, data/trash, data/workspace
    with subdirs data, docs, reports, skills), writes a .gitignore for the
    workspace, runs git init, and makes an initial commit.
    Skips gracefully if already initialized.
#>

$ErrorActionPreference = "Stop"

$scriptRoot  = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
$dataRoot    = Join-Path $projectRoot "data"
$workspace   = Join-Path $dataRoot "workspace"

Write-Host ""
Write-Host "=== Initializing Data Directory ===" -ForegroundColor Cyan
Write-Host "  Path: $dataRoot"

# Create top-level data directories
foreach ($dir in @("logs", "trash", "workspace")) {
    $p = Join-Path $dataRoot $dir
    if (-not (Test-Path $p)) {
        New-Item -ItemType Directory -Force -Path $p | Out-Null
        Write-Host "  Created: data/$dir" -ForegroundColor DarkGray
    }
}

# Create workspace subdirectories
foreach ($dir in @("data", "docs", "reports", "skills")) {
    $p = Join-Path $workspace $dir
    if (-not (Test-Path $p)) {
        New-Item -ItemType Directory -Force -Path $p | Out-Null
        Write-Host "  Created: data/workspace/$dir" -ForegroundColor DarkGray
    }
}

# Initialize workspace git repo
if (Test-Path (Join-Path $workspace ".git")) {
    Write-Host "  Workspace git already initialized. Skipping." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "=== Initializing Workspace Git ===" -ForegroundColor Cyan

Push-Location $workspace

try {
    git init -b main
    git config user.name  "Local AI Agent"
    git config user.email "local-agent@example.local"

    @"
*.tmp
*.swp
*.bak
.DS_Store
.vscode/
.idea/
.env
*.pem
*.key
id_rsa
id_ed25519
"@ | Set-Content -Encoding UTF8 ".gitignore"

    # Create a README for the skills directory
    @"
# Skills Directory

Place Python tool scripts here. Each script must contain:
- `SKILL_METADATA` dict (with `description` and `parameters`)
- `run(params: dict) -> dict` function

See the deployment docs for details.
"@ | Set-Content -Encoding UTF8 (Join-Path "skills" "README.md")

    git add .
    git commit -m "chore: initialize workspace"

    Write-Host ""
    Write-Host "Workspace initialized successfully." -ForegroundColor Green
} finally {
    Pop-Location
}

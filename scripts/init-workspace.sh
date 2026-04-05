#!/usr/bin/env bash
# Initialize data/ directory structure and workspace Git repository.
# Skips gracefully if already initialized.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_ROOT="$PROJECT_ROOT/data"
WORKSPACE="$DATA_ROOT/workspace"

echo ""
echo "=== Initializing Data Directory ==="
echo "  Path: $DATA_ROOT"

# Create top-level data directories
for dir in logs trash workspace; do
    mkdir -p "$DATA_ROOT/$dir"
    echo "  Ensured: data/$dir"
done

# Create workspace subdirectories
for dir in data docs reports skills; do
    mkdir -p "$WORKSPACE/$dir"
    echo "  Ensured: data/workspace/$dir"
done

# Initialize workspace git repo
if [ -d "$WORKSPACE/.git" ]; then
    echo "  Workspace git already initialized. Skipping."
    exit 0
fi

echo ""
echo "=== Initializing Workspace Git ==="

cd "$WORKSPACE"

git init -b main
git config user.name  "Local AI Agent"
git config user.email "local-agent@example.local"

cat > .gitignore << 'EOF'
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
EOF

# Create a README for the skills directory
cat > skills/README.md << 'EOF'
# Skills Directory

Place Python tool scripts here. Each script must contain:
- `SKILL_METADATA` dict (with `description` and `parameters`)
- `run(params: dict) -> dict` function

See the deployment docs for details.
EOF

git add .
git commit -m "chore: initialize workspace"

echo ""
echo "Workspace initialized successfully."

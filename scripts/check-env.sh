#!/usr/bin/env bash
# Check that all required tools are installed for local-ai-agent.
# Exits 0 if all pass, 1 if any fail.
set -euo pipefail

all_ok=true

check_tool() {
    local label="$1"
    local cmd="$2"
    shift 2
    local args=("$@")
    if output=$("$cmd" "${args[@]}" 2>&1 | head -1); then
        printf "  [PASS] %-18s %s\n" "$label:" "$output"
    else
        printf "  [FAIL] %-18s not found or error\n" "$label:"
        all_ok=false
    fi
}

check_python_pkg() {
    local label="$1"
    local module="$2"
    local python_cmd="${3:-python}"
    local ver
    if ver=$($python_cmd -c "import $module; print($module.__version__)" 2>/dev/null); then
        printf "  [PASS] %-18s v%s\n" "$label:" "$ver"
    else
        printf "  \033[33m[WARN]\033[0m %-18s not installed (run: pip install -r requirements.txt)\n" "$label:"
    fi
}

echo ""
echo "=== Environment Check ==="

check_tool "Docker"         docker  --version
check_tool "Docker Compose" docker  compose version
check_tool "Git"            git     --version
check_tool "Ollama"         ollama  list

# Python check
PYTHON_CMD="python"
command -v python &>/dev/null || PYTHON_CMD="python3"
check_tool "Python"         "$PYTHON_CMD" --version
check_tool "Node.js"        node --version
check_tool "npm"            npm --version

# Python package checks (non-fatal warnings)
echo ""
check_python_pkg "Textual" "textual" "$PYTHON_CMD"
check_python_pkg "httpx"   "httpx"   "$PYTHON_CMD"
if bff_deps=$($PYTHON_CMD -c "import fastapi, uvicorn; print(f'fastapi {fastapi.__version__}, uvicorn {uvicorn.__version__}')" 2>/dev/null); then
    printf "  [PASS] %-18s %s\n" "BFF deps:" "$bff_deps"
else
    printf "  \033[33m[WARN]\033[0m %-18s not installed (run: pip install -r requirements.txt)\n" "BFF deps:"
fi

if [ -f "$(cd "$(dirname "$0")/.." && pwd)/apps/web/package.json" ]; then
    printf "  [PASS] %-18s apps/web\n" "Web package:"
else
    printf "  \033[33m[WARN]\033[0m %-18s apps/web/package.json not found\n" "Web package:"
fi

echo ""
if [ "$all_ok" = "true" ]; then
    echo "All checks passed."
    exit 0
else
    echo "One or more checks FAILED. Install the missing tools and retry."
    exit 1
fi

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

echo ""
echo "=== Environment Check ==="

check_tool "Docker"         docker  --version
check_tool "Docker Compose" docker  compose version
check_tool "Git"            git     --version
check_tool "Ollama"         ollama  list

echo ""
if [ "$all_ok" = "true" ]; then
    echo "All checks passed."
    exit 0
else
    echo "One or more checks FAILED. Install the missing tools and retry."
    exit 1
fi

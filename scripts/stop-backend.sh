#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

read_env_value() {
    local key="$1" default="$2"
    grep -oP "^${key}=\K.+" "$ENV_FILE" 2>/dev/null | head -n 1 || echo "$default"
}

get_listening_process_id() {
    local port="$1"
    if command -v powershell.exe >/dev/null 2>&1; then
        powershell.exe -NoProfile -Command "\$c=Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess; if (\$c) { Write-Output \$c }" 2>/dev/null | tr -d '\r'
        return 0
    fi
    if command -v lsof >/dev/null 2>&1; then
        lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1
        return 0
    fi
    if command -v ss >/dev/null 2>&1; then
        ss -ltnp "( sport = :$port )" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -n 1
        return 0
    fi
}

PORT="${1:-$(read_env_value BFF_PORT 9510)}"
PID="$(get_listening_process_id "$PORT" | head -n 1)"

printf '\n\033[36m:: Stopping Python BFF only\033[0m\n'
if [[ -z "$PID" ]]; then
    printf '   \033[33m[WARN]\033[0m No Python BFF listener found on port %s\n' "$PORT"
    exit 0
fi

if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue" >/dev/null 2>&1 || true
else
    kill "$PID" >/dev/null 2>&1 || true
fi

printf '   \033[32m[OK]\033[0m Stopped Python BFF process %s on port %s\n' "$PID" "$PORT"
printf '   Docker containers and Ollama were not touched.\n'
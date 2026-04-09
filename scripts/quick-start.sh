#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
#  Local AI Agent - Quick Start (Git Bash / WSL on Windows)
#
#  1. Starts Docker Desktop if not already running
#  2. Checks Ollama and starts it if not running
#  3. Launches open-webui serve in a new terminal (via conda)
#  4. Brings up local-ai-agent Docker containers
#  5. Polls Open WebUI until responsive, then opens the browser
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────
CONDA_ROOT="${CONDA_ROOT:-/c/ProgramData/miniconda3}"
CONDA_ENV="${CONDA_ENV:-open-webui}"
OPEN_WEBUI_PORT="${OPEN_WEBUI_PORT:-8080}"
TIMEOUT_SEC="${TIMEOUT_SEC:-180}"
DO_BUILD="${BUILD:-0}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OPEN_WEBUI_URL="http://localhost:${OPEN_WEBUI_PORT}"

# ── Helpers ───────────────────────────────────────────────────────────────
step()  { printf '\n\033[36m:: %s\033[0m\n' "$1"; }
ok()    { printf '   \033[32m[OK]\033[0m %s\n' "$1"; }
wait_()  { printf '   \033[90m... %s\033[0m\n' "$1"; }
fail()  { printf '   \033[31m[FAIL]\033[0m %s\n' "$1"; }

wait_until() {
    local timeout=$1 label=$2 interval=${3:-3} elapsed=0
    shift 3 || shift $#
    while (( elapsed < timeout )); do
        if "$@" 2>/dev/null; then return 0; fi
        wait_ "$label (${elapsed}s / ${timeout}s)"
        sleep "$interval"
        elapsed=$(( elapsed + interval ))
    done
    return 1
}

docker_ready() { docker info &>/dev/null; }
webui_ready()  { curl -sf --max-time 3 "$OPEN_WEBUI_URL" >/dev/null 2>&1; }
ollama_ready() { curl -sf --max-time 3 "http://localhost:11434" >/dev/null 2>&1; }

# ══════════════════════════════════════════════════════════════════════════
echo ""
echo "========================================"
echo "  Local AI Agent - Quick Start"
echo "========================================"

# ── 1. Docker Desktop ────────────────────────────────────────────────────
step "Checking Docker Desktop"

if docker_ready; then
    ok "Docker daemon already running"
else
    DD_PATH="/c/Program Files/Docker/Docker/Docker Desktop.exe"
    if [[ ! -f "$DD_PATH" ]]; then
        fail "Docker Desktop not found at $DD_PATH"; exit 1
    fi
    wait_ "Starting Docker Desktop ..."
    start "" "$DD_PATH" 2>/dev/null || "$DD_PATH" &

    if ! wait_until 120 "Waiting for Docker daemon" 3 docker_ready; then
        fail "Docker daemon did not start within 120 seconds"; exit 1
    fi
    ok "Docker daemon is ready"
fi

# ── 2. Ollama ─────────────────────────────────────────────────────────────
step "Checking Ollama"

OLLAMA_URL="http://localhost:11434"

if ollama_ready; then
    ok "Ollama already running at $OLLAMA_URL"
else
    if ! command -v ollama &>/dev/null; then
        fail "Ollama not found. Install from https://ollama.com/download"
        exit 1
    fi

    wait_ "Starting Ollama serve ..."
    ollama serve &>/dev/null &
    OLLAMA_PID=$!
    disown "$OLLAMA_PID" 2>/dev/null

    if ! wait_until 60 "Waiting for Ollama" 3 ollama_ready; then
        fail "Ollama did not start within 60 seconds"
        exit 1
    fi
    ok "Ollama is ready"
fi

# Check configured model availability
OLLAMA_MODEL=$(grep -oP 'OLLAMA_MODEL=\K.+' "$PROJECT_ROOT/.env" 2>/dev/null || echo "")
if [[ -n "$OLLAMA_MODEL" ]]; then
    if ollama list 2>/dev/null | grep -qF "$OLLAMA_MODEL"; then
        ok "Model '$OLLAMA_MODEL' is available"
    else
        printf '   \033[33m[WARN]\033[0m Model '\''%s'\'' not found locally. You may need to run: ollama pull %s\n' "$OLLAMA_MODEL" "$OLLAMA_MODEL"
    fi
fi

# ── 3. Open WebUI (conda) ────────────────────────────────────────────────
step "Launching Open WebUI (conda env: $CONDA_ENV)"

if webui_ready; then
    ok "Open WebUI already running at $OPEN_WEBUI_URL"
    ALREADY_RUNNING=1
else
    ALREADY_RUNNING=0
    # Convert conda root to Windows path for cmd.exe
    CONDA_WIN="${CONDA_ROOT//\//\\}"
    CONDA_WIN="${CONDA_WIN/\\c\\/C:\\}"

    # Launch in a new cmd.exe window (same as Anaconda Prompt, avoids PS execution-policy)
    start "" cmd.exe /K \
        "title Open WebUI Server && \"${CONDA_WIN}\\Scripts\\activate.bat\" ${CONDA_WIN} && conda activate ${CONDA_ENV} && echo. && echo Starting open-webui serve ... && echo. && open-webui serve" \
        2>/dev/null \
    || {
        fail "Could not launch Open WebUI window"; exit 1
    }
    ok "Open WebUI launched in new Anaconda Prompt window"
fi

# ── 4. Web Search option ──────────────────────────────────────────────────
step "Web Search (SearXNG)"

ENABLE_WEBSEARCH=0
printf "   是否启用联网搜索功能? (Y/N, 默认 N): "
read -r ws_choice
if [[ "$ws_choice" =~ ^[Yy] ]]; then
    ENABLE_WEBSEARCH=1
    ok "Web Search 已启用"
    sed -i 's/ENABLE_WEBSEARCH=.*/ENABLE_WEBSEARCH=true/' "$PROJECT_ROOT/.env"
else
    ok "Web Search 已跳过 (可稍后在 .env 中设置 ENABLE_WEBSEARCH=true)"
    sed -i 's/ENABLE_WEBSEARCH=.*/ENABLE_WEBSEARCH=false/' "$PROJECT_ROOT/.env"
fi

# ── 5. Docker Compose ────────────────────────────────────────────────────
step "Starting local-ai-agent containers"

cd "$PROJECT_ROOT"
COMPOSE_ARGS=""
[[ "$ENABLE_WEBSEARCH" -eq 1 ]] && COMPOSE_ARGS="--profile websearch"
COMPOSE_UP_ARGS="up -d"
[[ "$DO_BUILD" == "1" ]] && COMPOSE_UP_ARGS="up -d --build"

docker compose $COMPOSE_ARGS $COMPOSE_UP_ARGS 2>&1 | sed 's/^/   /'

if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
    fail "docker compose up failed"; exit 1
fi
ok "Containers are up"

# ── 6. Wait for Open WebUI ───────────────────────────────────────────────
if [[ "$ALREADY_RUNNING" -eq 0 ]]; then
    step "Waiting for Open WebUI to become ready"
    if ! wait_until "$TIMEOUT_SEC" "Polling $OPEN_WEBUI_URL" 3 webui_ready; then
        fail "Open WebUI did not respond within ${TIMEOUT_SEC}s"
        echo "   It may still be loading. Try opening $OPEN_WEBUI_URL manually."
        exit 1
    fi
    ok "Open WebUI is ready"
fi

# ── 7. Open browser ──────────────────────────────────────────────────────
step "Opening browser"
start "" "$OPEN_WEBUI_URL" 2>/dev/null \
    || cmd.exe /c start "$OPEN_WEBUI_URL" 2>/dev/null \
    || xdg-open "$OPEN_WEBUI_URL" 2>/dev/null \
    || echo "   Please open $OPEN_WEBUI_URL manually."
ok "Launched $OPEN_WEBUI_URL"

# ── Summary ───────────────────────────────────────────────────────────────
GW_PORT=$(grep -oP 'GATEWAY_PORT=\K\d+' "$PROJECT_ROOT/.env" 2>/dev/null || echo "8400")

echo ""
echo "========================================"
printf '  \033[32mAll services started successfully!\033[0m\n'
echo "========================================"
echo ""
echo "  Open WebUI  :  $OPEN_WEBUI_URL"
echo "  AI Gateway  :  http://localhost:$GW_PORT"
[[ "$ENABLE_WEBSEARCH" -eq 1 ]] && echo "  Web Search  :  Enabled (SearXNG)"
echo ""
echo "  Tip: Close the Open WebUI window to stop the server,"
echo "       then run: docker compose down"
echo ""

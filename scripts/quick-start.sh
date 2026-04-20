#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
#  Local AI Agent v2.0 - Quick Start (Git Bash / WSL on Windows)
#
#  1. Starts Docker Desktop if not already running
#  2. Checks Ollama and starts it if not running
#  3. Checks Python & Textual
#  4. Asks whether to enable web search
#  5. Brings up Docker skill containers
#  6. Waits for skill services to become healthy
#  7. Launches TUI (python -m tui)
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────
TIMEOUT_SEC="${TIMEOUT_SEC:-120}"
DO_BUILD="${BUILD:-0}"
SKIP_TUI="${SKIP_TUI:-0}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

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
ollama_ready() { curl -sf --max-time 3 "http://localhost:11434" >/dev/null 2>&1; }

# Read port from .env, with fallback
read_env_port() {
    local key="$1" default="$2"
    grep -oP "${key}=\K\d+" "$PROJECT_ROOT/.env" 2>/dev/null || echo "$default"
}

skill_files_healthy() {
    local port
    port=$(read_env_port SKILL_FILES_PORT 9101)
    curl -sf --max-time 3 "http://localhost:${port}/health" >/dev/null 2>&1
}
skill_runner_healthy() {
    local port
    port=$(read_env_port SKILL_RUNNER_PORT 9102)
    curl -sf --max-time 3 "http://localhost:${port}/health" >/dev/null 2>&1
}

# ══════════════════════════════════════════════════════════════════════════
echo ""
echo "========================================"
echo "  Local AI Agent v2.0 - Quick Start"
echo "========================================"

# ── 0. Ensure data/ directory exists ──────────────────────────────────────
if [ ! -d "$PROJECT_ROOT/data/workspace" ]; then
    step "Initializing data directory (first run)"
    bash "$SCRIPT_DIR/init-workspace.sh"
fi

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

if ollama_ready; then
    ok "Ollama already running at http://localhost:11434"
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

# ── 3. Python & Textual check ────────────────────────────────────────────
step "Checking Python environment"

if ! command -v python &>/dev/null && ! command -v python3 &>/dev/null; then
    fail "Python not found. Install Python 3.11+ from https://python.org"
    exit 1
fi

PYTHON_CMD="python"
command -v python &>/dev/null || PYTHON_CMD="python3"

PY_VER=$($PYTHON_CMD --version 2>&1)
ok "Python: $PY_VER"

TEXTUAL_VER=$($PYTHON_CMD -c "import textual; print(textual.__version__)" 2>/dev/null || echo "")
if [[ -n "$TEXTUAL_VER" ]]; then
    ok "Textual: v$TEXTUAL_VER"
else
    printf '   \033[33m[WARN]\033[0m Textual not installed. Run: pip install -r requirements.txt\n'
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

# ── 5. Docker Compose (skill services only) ──────────────────────────────
step "Starting skill service containers"

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

# ── 6. Wait for skill services to become healthy ─────────────────────────
step "Waiting for skill services to become healthy"

check_skills_healthy() {
    skill_files_healthy && skill_runner_healthy
}

if ! wait_until "$TIMEOUT_SEC" "Polling skill-files & skill-runner health" 3 check_skills_healthy; then
    fail "Skill services did not become healthy within ${TIMEOUT_SEC}s"
    echo "   Check: docker compose ps / docker compose logs"
    exit 1
fi
ok "All skill services are healthy"

# ── 7. Summary ────────────────────────────────────────────────────────────
SF_PORT=$(read_env_port SKILL_FILES_PORT 9101)
SR_PORT=$(read_env_port SKILL_RUNNER_PORT 9102)
SW_PORT=$(read_env_port SKILL_WEBSEARCH_PORT 9103)

echo ""
echo "========================================"
printf '  \033[32mAll services started successfully!\033[0m\n'
echo "========================================"
echo ""
echo "  skill-files   :  http://localhost:$SF_PORT"
echo "  skill-runner  :  http://localhost:$SR_PORT"
[[ "$ENABLE_WEBSEARCH" -eq 1 ]] && echo "  skill-websearch: http://localhost:$SW_PORT"
echo "  Ollama        :  http://localhost:11434"
echo ""
echo "  Stop services: docker compose down"
echo ""

# ── 8. Launch TUI ─────────────────────────────────────────────────────────
if [[ "$SKIP_TUI" -eq 1 ]]; then
    step "Skipping TUI launch (SKIP_TUI=1)"
else
    step "Launching TUI"
    cd "$PROJECT_ROOT"
    $PYTHON_CMD -m tui
fi

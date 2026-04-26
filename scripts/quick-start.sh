#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Local AI Agent v2.2 - Quick Start (Git Bash / WSL)
#
# 1. Starts Docker Desktop if not already running
# 2. Checks Ollama and starts it if not running
# 3. Checks Python, BFF dependencies, Node.js, and npm
# 4. Asks whether to enable web search
# 5. Brings up Docker skill containers
# 6. Waits for skill services to become healthy
# 7. Starts the Python BFF adapter if needed
# 8. Installs Web frontend dependencies if missing
# 9. Launches the React/Vite Web UI and opens the browser
# ---------------------------------------------------------------------------
set -euo pipefail

TIMEOUT_SEC="${TIMEOUT_SEC:-120}"
DO_BUILD="${BUILD:-0}"
SKIP_FRONTEND="${SKIP_FRONTEND:-${SKIP_CLI:-${SKIP_TUI:-${SKIP_UI:-0}}}}"
LAUNCH_CLI="${LAUNCH_CLI:-0}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
STARTED_BFF=0
STOP_BFF_ON_EXIT=0
BFF_PID=""
STARTED_WEB=0
WEB_PID=""
WEB_HOST="127.0.0.1"
WEB_PORT="${WEB_PORT:-5173}"
WEB_URL="http://${WEB_HOST}:${WEB_PORT}"

step() { printf '\n\033[36m:: %s\033[0m\n' "$1"; }
ok() { printf '   \033[32m[OK]\033[0m %s\n' "$1"; }
wait_() { printf '   \033[90m... %s\033[0m\n' "$1"; }
warn() { printf '   \033[33m[WARN]\033[0m %s\n' "$1"; }
fail() { printf '   \033[31m[FAIL]\033[0m %s\n' "$1"; }

cleanup() {
    if [[ "$STARTED_BFF" == "1" && "$STOP_BFF_ON_EXIT" == "1" && -n "$BFF_PID" ]]; then
        kill "$BFF_PID" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

wait_until() {
    local timeout=$1 label=$2 interval=${3:-3} elapsed=0
    shift 3 || true
    while (( elapsed < timeout )); do
        if "$@" >/dev/null 2>&1; then return 0; fi
        wait_ "$label (${elapsed}s / ${timeout}s)"
        sleep "$interval"
        elapsed=$(( elapsed + interval ))
    done
    return 1
}

read_env_value() {
    local key="$1" default="$2"
    grep -oP "^${key}=\K.+" "$ENV_FILE" 2>/dev/null | head -n 1 || echo "$default"
}

prepend_path() {
    local dir="$1"
    [[ -z "$dir" ]] && return 0
    case ":$PATH:" in
        *":$dir:"*) ;;
        *) PATH="$dir:$PATH" ;;
    esac
}

docker_ready() { docker info >/dev/null 2>&1; }
ollama_ready() { curl -sf --max-time 3 "http://localhost:11434" >/dev/null 2>&1; }

skill_files_healthy() {
    local port
    port=$(read_env_value SKILL_FILES_PORT 9101)
    curl -sf --max-time 3 "http://localhost:${port}/health" >/dev/null 2>&1
}

skill_runner_healthy() {
    local port
    port=$(read_env_value SKILL_RUNNER_PORT 9102)
    curl -sf --max-time 3 "http://localhost:${port}/health" >/dev/null 2>&1
}

bff_ready() {
    local host port
    host=$(read_env_value BFF_HOST 127.0.0.1)
    port=$(read_env_value BFF_PORT 9510)
    [[ "$host" == "0.0.0.0" ]] && host="127.0.0.1"
    curl -sf --max-time 3 "http://${host}:${port}/health" >/dev/null 2>&1
}

web_ready() { curl -sf --max-time 3 "$WEB_URL" >/dev/null 2>&1; }

web_ready_on_port() {
    local port="$1"
    curl -sf --max-time 3 "http://${WEB_HOST}:${port}" >/dev/null 2>&1
}

port_available() {
    local port="$1"
    "$PYTHON_CMD" - "$WEB_HOST" "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

choose_web_port() {
    local preferred="$1"
    local seen="" port
    for port in "$preferred" {5173..5300} {3000..3020}; do
        case " $seen " in
            *" $port "*) continue ;;
        esac
        seen="${seen} ${port}"
        if port_available "$port"; then
            printf '%s\n' "$port"
            return 0
        fi
    done
    return 1
}

join_unique_origins() {
    local result="" value part trimmed
    for value in "$@"; do
        IFS=',' read -ra parts <<< "$value"
        for part in "${parts[@]}"; do
            trimmed="$(printf '%s' "$part" | sed 's/^ *//;s/ *$//')"
            [[ -z "$trimmed" ]] && continue
            case ",$result," in
                *",$trimmed,"*) ;;
                *) result="${result:+$result,}$trimmed" ;;
            esac
        done
    done
    printf '%s\n' "$result"
}

open_browser() {
    if command -v powershell.exe >/dev/null 2>&1; then
        powershell.exe -NoProfile -Command "Start-Process '$WEB_URL'" >/dev/null 2>&1 || true
    elif command -v cmd.exe >/dev/null 2>&1; then
        cmd.exe /c start "" "$WEB_URL" >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$WEB_URL" >/dev/null 2>&1 || true
    fi
}

echo ""
echo "========================================"
echo "  Local AI Agent v2.2 - Quick Start"
echo "========================================"

if [[ ! -d "$PROJECT_ROOT/data/workspace" ]]; then
    step "Initializing workspace data directory"
    bash "$SCRIPT_DIR/init-workspace.sh"
fi

step "Checking Docker Desktop"
if docker_ready; then
    ok "Docker daemon already running"
else
    DD_PATH="/c/Program Files/Docker/Docker/Docker Desktop.exe"
    if [[ ! -f "$DD_PATH" ]]; then
        fail "Docker Desktop not found at $DD_PATH"
        exit 1
    fi
    wait_ "Starting Docker Desktop"
    start "" "$DD_PATH" 2>/dev/null || "$DD_PATH" &
    if ! wait_until 120 "Waiting for Docker daemon" 3 docker_ready; then
        fail "Docker daemon did not start within 120 seconds"
        exit 1
    fi
    ok "Docker daemon is ready"
fi

step "Checking Ollama"
if ollama_ready; then
    ok "Ollama already running at http://localhost:11434"
else
    if ! command -v ollama >/dev/null 2>&1; then
        fail "Ollama not found. Install it from https://ollama.com/download"
        exit 1
    fi
    wait_ "Starting Ollama serve"
    ollama serve >/dev/null 2>&1 &
    disown "$!" 2>/dev/null || true
    if ! wait_until 60 "Waiting for Ollama" 3 ollama_ready; then
        fail "Ollama did not start within 60 seconds"
        exit 1
    fi
    ok "Ollama is ready"
fi

OLLAMA_MODEL=$(read_env_value OLLAMA_MODEL "")
if [[ -n "$OLLAMA_MODEL" ]]; then
    if ollama list 2>/dev/null | grep -qF "$OLLAMA_MODEL"; then
        ok "Model '$OLLAMA_MODEL' is available"
    else
        printf '   \033[33m[WARN]\033[0m Model '\''%s'\'' not found locally. You may need: ollama pull %s\n' "$OLLAMA_MODEL" "$OLLAMA_MODEL"
    fi
fi

step "Checking Python environment"
if command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
else
    fail "Python not found. Install Python 3.11+"
    exit 1
fi

PY_VER=$($PYTHON_CMD -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>&1 || true)
if [[ -z "$PY_VER" ]]; then
    fail "Python is not usable in the current shell"
    exit 1
fi
ok "Python: $PY_VER"

BFF_DEPS=$($PYTHON_CMD -c "import fastapi, uvicorn; print(f'fastapi {fastapi.__version__}, uvicorn {uvicorn.__version__}')" 2>&1 || true)
if [[ -z "$BFF_DEPS" ]]; then
    fail "Required BFF packages are missing. Run: python -m pip install -r requirements.txt"
    exit 1
fi
ok "BFF deps: $BFF_DEPS"

step "Checking Node.js environment"
if ! command -v node >/dev/null 2>&1; then
    if [[ -d "/c/Program Files/nodejs" ]]; then
        prepend_path "/c/Program Files/nodejs"
    elif [[ -d "/mnt/c/Program Files/nodejs" ]]; then
        prepend_path "/mnt/c/Program Files/nodejs"
    fi
fi

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    fail "Node.js or npm not found. Install Node.js LTS on the host system"
    exit 1
fi

NODE_VER=$(node -p "process.version" 2>&1 || true)
NPM_VER=$(npm -v 2>&1 || true)
if [[ -z "$NODE_VER" || -z "$NPM_VER" ]]; then
    fail "Node.js exists but is not runnable in the current shell"
    exit 1
fi
ok "Node.js: $NODE_VER"
ok "npm: $NPM_VER"

PREFERRED_WEB_PORT=$(read_env_value WEB_PORT "$WEB_PORT")
if [[ ! "$PREFERRED_WEB_PORT" =~ ^[0-9]+$ ]]; then
    PREFERRED_WEB_PORT=5173
fi

if ! WEB_PORT=$(choose_web_port "$PREFERRED_WEB_PORT"); then
    fail "No available Web UI port found in 5173..5300 or 3000..3020"
    exit 1
fi
WEB_URL="http://${WEB_HOST}:${WEB_PORT}"
if [[ "$WEB_PORT" != "$PREFERRED_WEB_PORT" ]]; then
    warn "Preferred Web UI port $PREFERRED_WEB_PORT is unavailable; using $WEB_PORT instead."
fi

CONFIGURED_WEB_ORIGINS=$(read_env_value WEB_ORIGINS "")
export WEB_ORIGINS
WEB_ORIGINS=$(join_unique_origins \
    "$CONFIGURED_WEB_ORIGINS" \
    "http://127.0.0.1:${WEB_PORT}" \
    "http://localhost:${WEB_PORT}" \
    "http://127.0.0.1:5173" \
    "http://localhost:5173")

step "Web search (SearXNG)"
printf "   Enable web search? (Y/N, default N): "
read -r ws_choice
ENABLE_WEBSEARCH=0
if [[ "$ws_choice" =~ ^[Yy] ]]; then
    ENABLE_WEBSEARCH=1
    ok "Web search enabled"
    sed -i 's/ENABLE_WEBSEARCH=.*/ENABLE_WEBSEARCH=true/' "$ENV_FILE"
else
    ok "Web search disabled"
    sed -i 's/ENABLE_WEBSEARCH=.*/ENABLE_WEBSEARCH=false/' "$ENV_FILE"
fi

step "Starting skill service containers"
cd "$PROJECT_ROOT"
COMPOSE_ARGS=(compose)
if [[ "$ENABLE_WEBSEARCH" == "1" ]]; then
    COMPOSE_ARGS+=(--profile websearch)
fi
COMPOSE_ARGS+=(up -d --remove-orphans)
if [[ "$DO_BUILD" == "1" ]]; then
    COMPOSE_ARGS+=(--build)
fi

docker "${COMPOSE_ARGS[@]}" 2>&1 | sed 's/^/   /'
if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
    fail "docker compose up failed"
    exit 1
fi
ok "Containers are up"

step "Waiting for skill services to become healthy"
check_skills_healthy() {
    skill_files_healthy && skill_runner_healthy
}

if ! wait_until "$TIMEOUT_SEC" "Polling skill-files and skill-runner health" 3 check_skills_healthy; then
    fail "Skill services did not become healthy within ${TIMEOUT_SEC}s"
    echo "   Check: docker compose ps / docker compose logs"
    exit 1
fi
ok "All skill services are healthy"

step "Checking Python frontend adapter (BFF)"
BFF_HOST=$(read_env_value BFF_HOST 127.0.0.1)
BFF_PORT=$(read_env_value BFF_PORT 9510)
[[ "$BFF_HOST" == "0.0.0.0" ]] && BFF_HOST="127.0.0.1"
BFF_URL="http://${BFF_HOST}:${BFF_PORT}"

if bff_ready; then
    ok "BFF already running at $BFF_URL"
else
    wait_ "Starting Python BFF"
    (cd "$PROJECT_ROOT" && "$PYTHON_CMD" -m bff >/dev/null 2>&1) &
    BFF_PID=$!
    STARTED_BFF=1
    STOP_BFF_ON_EXIT=1
    if ! wait_until 30 "Waiting for BFF health" 3 bff_ready; then
        fail "BFF did not become healthy within 30 seconds"
        exit 1
    fi
    ok "BFF is ready at $BFF_URL"
fi

WEB_DIR="$PROJECT_ROOT/apps/web"
INK_DIR="$PROJECT_ROOT/apps/cli-ink"

if [[ "$SKIP_FRONTEND" != "1" ]]; then
    if [[ "$LAUNCH_CLI" == "1" ]]; then
        step "Checking legacy Ink CLI dependencies"
        if [[ ! -d "$INK_DIR" ]]; then
            fail "Ink frontend directory not found: $INK_DIR"
            exit 1
        fi

        if [[ ! -d "$INK_DIR/node_modules/ink" ]]; then
            wait_ "Installing Ink frontend dependencies"
            (cd "$INK_DIR" && npm install) 2>&1 | sed 's/^/   /'
            if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
                fail "npm install failed"
                exit 1
            fi
            ok "Ink frontend dependencies installed"
        else
            ok "Ink frontend dependencies already installed"
        fi
    else
        step "Checking Web UI dependencies"
        if [[ ! -d "$WEB_DIR" ]]; then
            fail "Web frontend directory not found: $WEB_DIR"
            exit 1
        fi

        if [[ ! -d "$WEB_DIR/node_modules/vite" ]]; then
            wait_ "Installing Web frontend dependencies"
            (cd "$WEB_DIR" && npm install) 2>&1 | sed 's/^/   /'
            if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
                fail "npm install failed"
                exit 1
            fi
            ok "Web frontend dependencies installed"
        else
            ok "Web frontend dependencies already installed"
        fi
    fi
fi

SF_PORT=$(read_env_value SKILL_FILES_PORT 9101)
SR_PORT=$(read_env_value SKILL_RUNNER_PORT 9102)
SW_PORT=$(read_env_value SKILL_WEBSEARCH_PORT 9103)

echo ""
echo "========================================"
printf '  \033[32mAll services started successfully!\033[0m\n'
echo "========================================"
echo ""
echo "  skill-files   :  http://localhost:$SF_PORT"
echo "  skill-runner  :  http://localhost:$SR_PORT"
[[ "$ENABLE_WEBSEARCH" == "1" ]] && echo "  skill-websearch: http://localhost:$SW_PORT"
echo "  Ollama        :  http://localhost:11434"
echo "  BFF           :  $BFF_URL"
if [[ "$SKIP_FRONTEND" != "1" && "$LAUNCH_CLI" != "1" ]]; then
    echo "  Web UI        :  $WEB_URL"
fi
echo ""
echo "  Stop services: docker compose down"
echo "  Backend-only : SKIP_FRONTEND=1 scripts/quick-start.sh"
echo "  Legacy CLI   : LAUNCH_CLI=1 scripts/quick-start.sh"
echo ""

if [[ "$SKIP_FRONTEND" == "1" ]]; then
    STOP_BFF_ON_EXIT=0
    [[ -n "$BFF_PID" ]] && disown "$BFF_PID" 2>/dev/null || true
    step "Skipping frontend launch (SKIP_FRONTEND=1 / legacy SKIP_CLI=1)"
elif [[ "$LAUNCH_CLI" == "1" ]]; then
    step "Launching legacy Ink CLI"
    cd "$INK_DIR"
    LOCAL_AI_AGENT_API_URL="$BFF_URL" npm run dev
else
    step "Launching Web UI"
    mkdir -p "$PROJECT_ROOT/data/logs"
    if web_ready; then
        ok "Web UI already running at $WEB_URL"
    else
        wait_ "Starting Vite dev server"
        (cd "$WEB_DIR" && WEB_PORT="$WEB_PORT" VITE_LOCAL_AI_AGENT_API_URL="$BFF_URL" npm run dev -- --host "$WEB_HOST" --port "$WEB_PORT" > "$PROJECT_ROOT/data/logs/web-ui.log" 2>&1) &
        WEB_PID=$!
        STARTED_WEB=1
        disown "$WEB_PID" 2>/dev/null || true

        if ! wait_until 45 "Waiting for Web UI" 3 web_ready; then
            [[ -n "$WEB_PID" ]] && kill "$WEB_PID" >/dev/null 2>&1 || true
            fail "Web UI did not become ready within 45 seconds"
            echo "   Check log: $PROJECT_ROOT/data/logs/web-ui.log"
            exit 1
        fi
        ok "Web UI is ready at $WEB_URL"
    fi

    STOP_BFF_ON_EXIT=0
    [[ -n "$BFF_PID" ]] && disown "$BFF_PID" 2>/dev/null || true
    open_browser
    ok "Browser opened: $WEB_URL"
    [[ -n "$BFF_PID" ]] && echo "   BFF process id: $BFF_PID"
    [[ -n "$WEB_PID" ]] && echo "   Web process id: $WEB_PID"
fi
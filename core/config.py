"""Centralised configuration for the core library.

All paths are resolved relative to the project root so the package works
both inside Docker containers and as a local Python import.
"""

from __future__ import annotations

import os
from pathlib import Path

# Project root: two levels up from this file (core/config.py → local-ai-agent/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(_PROJECT_ROOT / ".env")


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    return int(os.environ.get(key, str(default)))


def _env_float(key: str, default: float) -> float:
    return float(os.environ.get(key, str(default)))


def _env_bool(key: str, default: bool) -> bool:
    return os.environ.get(key, str(default)).lower() in ("true", "1", "yes")


def _env_list(key: str, default: str) -> list[str]:
    value = os.environ.get(key, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _default_web_origins() -> str:
    # BFF port first: the production build is served by the BFF itself, so the
    # browser talks to one origin only. The 5173+ ranges stay for `npm run dev`.
    origins = [f"http://127.0.0.1:{BFF_PORT}", f"http://localhost:{BFF_PORT}"]
    ports = list(dict.fromkeys([*range(5173, 5301), *range(3000, 3021)]))
    for port in ports:
        origins.append(f"http://127.0.0.1:{port}")
        origins.append(f"http://localhost:{port}")
    return ",".join(origins)


# ── Paths ────────────────────────────────────────────────────────────────

PROJECT_ROOT = _PROJECT_ROOT

WORKSPACE_PATH = Path(_env("WORKSPACE_PATH", str(_PROJECT_ROOT / "data" / "workspace")))
TOOLS_DIR = Path(_env("TOOLS_DIR", str(_PROJECT_ROOT / "config" / "tools")))
POLICY_PATH = Path(_env("POLICY_PATH", str(_PROJECT_ROOT / "config" / "policy.yaml")))
LOG_PATH = Path(_env("LOG_PATH", str(_PROJECT_ROOT / "data" / "logs" / "audit.jsonl")))
PROMPTS_DIR = Path(_env("PROMPTS_DIR", str(_PROJECT_ROOT / "config" / "prompts")))
DB_PATH = Path(_env("DB_PATH", str(_PROJECT_ROOT / "data" / "conversations.db")))

# ── Service URLs ─────────────────────────────────────────────────────────

OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "gemma4:26b")
OLLAMA_READ_TIMEOUT = _env_int("OLLAMA_READ_TIMEOUT", 600)

SKILL_FILES_URL = _env("SKILL_FILES_URL", "http://localhost:9101")
SKILL_RUNNER_URL = _env("SKILL_RUNNER_URL", "http://localhost:9102")
SKILL_WEBSEARCH_URL = _env("SKILL_WEBSEARCH_URL", "http://localhost:9103")

# ── Feature flags ────────────────────────────────────────────────────────

ENABLE_WEBSEARCH = _env_bool("ENABLE_WEBSEARCH", False)
AUTO_GIT_COMMIT = _env_bool("AUTO_GIT_COMMIT", True)
# Close-page auto exit: after the page has been opened once, stopping heartbeats
# for this long shuts the whole stack down (stock finishes current work first).
AUTO_EXIT_ON_CLOSE = _env_bool("AUTO_EXIT_ON_CLOSE", True)
HEARTBEAT_TIMEOUT_SECONDS = _env_float("HEARTBEAT_TIMEOUT_SECONDS", 15.0)


# ── Context management ───────────────────────────────────────────────────

CONTEXT_WINDOW = _env_int("CONTEXT_WINDOW", 32768)
# Compaction is a fallback for a full window, not a routine budget. Raised so a
# long research turn is not summarized away while the model still has room.
COMPACT_THRESHOLD = _env_float("COMPACT_THRESHOLD", 0.9)

# ── Output limits (0 = unlimited) ────────────────────────────────────────
# These bound what the model may see or emit. 0 means "no cap"; the harness must
# not silently shorten provider output, tool results or drafts.

# Per-tool result characters handed back to the model. 0 = full result.
TOOL_RESULT_MAX_CHARS = _env_int("TOOL_RESULT_MAX_CHARS", 0)
# Over-cap, non-paginated results are saved through the current workspace tools.
# Sent as the provider's max_tokens. 0 = let the provider decide.
MODEL_MAX_OUTPUT_TOKENS = _env_int("MODEL_MAX_OUTPUT_TOKENS", 0)

# ── Tool tier ────────────────────────────────────────────────────────────

TOOL_TIER = _env("TOOL_TIER", "all")

# Tool-call rounds per request before the agent is asked to wrap up.
# 0 = unlimited; research turns already continue while evidence is pending.
AGENT_MAX_ROUNDS = _env_int("AGENT_MAX_ROUNDS", 0)

# Shared character budget for files auto-injected into one turn's context.
# 0 = no prefetch cap; disable prefetch entirely with PREFETCH_ENABLED=false.
PREFETCH_MAX_CHARS = _env_int("PREFETCH_MAX_CHARS", 0)
PREFETCH_ENABLED = _env_bool("PREFETCH_ENABLED", True)

# Web search/fetch call budgets per user message (fresh allowance each turn).
# 0 = unlimited.
WEB_SEARCH_BUDGET = _env_int("WEB_SEARCH_BUDGET", 0)
WEB_FETCH_BUDGET = _env_int("WEB_FETCH_BUDGET", 0)

# ── Publication gate ─────────────────────────────────────────────────────
# The independent review still runs and is still saved; this only decides
# whether its findings, or a mechanical check, can block publishing.
PUBLISH_REVIEW_ENFORCE = _env_bool("PUBLISH_REVIEW_ENFORCE", False)
# Quote provenance: every published quote/number must appear in text the tools
# actually returned. Kept on by default because it is the anti-fabrication root;
# set false to publish without that check.
PUBLISH_QUOTE_CHECK = _env_bool("PUBLISH_QUOTE_CHECK", True)

# Only explicitly designated workspaces may supply tool results to cloud models.
WORKSPACE_CLOUD_ALLOWED = _env_bool("WORKSPACE_CLOUD_ALLOWED", False)
# Second, independent grant: cloud models may modify the workspace / run code.
# Kept separate so read access never implies write access.
CLOUD_WRITE_ALLOWED = _env_bool("CLOUD_WRITE_ALLOWED", False)

# ── Logging ──────────────────────────────────────────────────────────────

LOG_LEVEL = _env("LOG_LEVEL", "INFO")

# ── BFF / Frontend adapter ───────────────────────────────────────────────

BFF_HOST = _env("BFF_HOST", "127.0.0.1")
BFF_PORT = _env_int("BFF_PORT", 9510)
WEB_ORIGINS = _env_list(
    "WEB_ORIGINS",
    _default_web_origins(),
)

# ── Memory ───────────────────────────────────────────────────────────────

SHARED_MEMORY_INDEX = "/workspace/.memory/MEMORY.md"
CONVERSATION_MEMORY_DIR = "/workspace/.memory/conversations"
CONVERSATION_MEMORY_INDEX = f"{CONVERSATION_MEMORY_DIR}/INDEX.md"
ACTIVE_CONVERSATION_LIMIT = _env_int("MEMORY_ACTIVE_LIMIT", 20)

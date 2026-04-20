"""Centralised configuration for the core library.

All paths are resolved relative to the project root so the package works
both inside Docker containers and as a local Python import.
"""

from __future__ import annotations

import os
from pathlib import Path

# Project root: two levels up from this file (core/config.py → local-ai-agent/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    return int(os.environ.get(key, str(default)))


def _env_float(key: str, default: float) -> float:
    return float(os.environ.get(key, str(default)))


def _env_bool(key: str, default: bool) -> bool:
    return os.environ.get(key, str(default)).lower() in ("true", "1", "yes")


# ── Paths ────────────────────────────────────────────────────────────────

PROJECT_ROOT = _PROJECT_ROOT

WORKSPACE_PATH = Path(_env("WORKSPACE_PATH", str(_PROJECT_ROOT / "data" / "workspace")))
TOOLS_DIR = Path(_env("TOOLS_DIR", str(_PROJECT_ROOT / "config" / "tools")))
POLICY_PATH = Path(_env("POLICY_PATH", str(_PROJECT_ROOT / "config" / "policy.yaml")))
LOG_PATH = Path(_env("LOG_PATH", str(_PROJECT_ROOT / "data" / "logs" / "audit.jsonl")))
PROMPTS_DIR = Path(_env("PROMPTS_DIR", str(_PROJECT_ROOT / "gateway" / "prompts")))
DB_PATH = Path(_env("DB_PATH", str(_PROJECT_ROOT / "data" / "conversations.db")))

# ── Service URLs ─────────────────────────────────────────────────────────

OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "gemma4:26b")

SKILL_FILES_URL = _env("SKILL_FILES_URL", "http://localhost:9101")
SKILL_RUNNER_URL = _env("SKILL_RUNNER_URL", "http://localhost:9102")
SKILL_WEBSEARCH_URL = _env("SKILL_WEBSEARCH_URL", "http://localhost:9103")

# ── Feature flags ────────────────────────────────────────────────────────

ENABLE_WEBSEARCH = _env_bool("ENABLE_WEBSEARCH", False)
AUTO_GIT_COMMIT = _env_bool("AUTO_GIT_COMMIT", True)

# ── Context management ───────────────────────────────────────────────────

CONTEXT_WINDOW = _env_int("CONTEXT_WINDOW", 32768)
COMPACT_THRESHOLD = _env_float("COMPACT_THRESHOLD", 0.6)

# ── Tool tier ────────────────────────────────────────────────────────────

TOOL_TIER = _env("TOOL_TIER", "core")

# ── Logging ──────────────────────────────────────────────────────────────

LOG_LEVEL = _env("LOG_LEVEL", "INFO")

# ── Memory ───────────────────────────────────────────────────────────────

SHARED_MEMORY_INDEX = "/workspace/.memory/MEMORY.md"
CONVERSATION_MEMORY_DIR = "/workspace/.memory/conversations"
CONVERSATION_MEMORY_INDEX = f"{CONVERSATION_MEMORY_DIR}/INDEX.md"
ACTIVE_CONVERSATION_LIMIT = _env_int("MEMORY_ACTIVE_LIMIT", 20)

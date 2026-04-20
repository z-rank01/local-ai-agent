"""AgentApp — Textual application entry point.

Bootstraps core services and launches the chat screen.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from textual.app import App

from core import config
from core.agent import Agent
from core.audit_logger import AuditLogger
from core.context_manager import ContextManager
from core.conversation_store import ConversationStore
from core.llm_client import LLMClient
from core.memory_manager import MemoryManager
from core.policy_engine import PolicyEngine
from core.prompt_builder import PromptBuilder
from core.tool_registry import ToolRegistry
from core.tool_router import ToolRouter

from tui.screens.chat_screen import ChatScreen

logger = logging.getLogger("tui")

_CSS_PATH = Path(__file__).parent / "styles" / "app.tcss"


class AgentApp(App):
    """Local AI Agent terminal interface."""

    TITLE = "Local AI Agent"
    CSS_PATH = str(_CSS_PATH) if _CSS_PATH.exists() else None
    ENABLE_COMMAND_PALETTE = False

    def __init__(self) -> None:
        super().__init__()
        self._setup_logging()

        # ── Core services ───────────────────────────────────────────
        enable_websearch = config.ENABLE_WEBSEARCH

        self._tool_registry = ToolRegistry(
            config.TOOLS_DIR, enable_websearch=enable_websearch
        )
        self._policy = PolicyEngine(config.POLICY_PATH)
        self._audit = AuditLogger(config.LOG_PATH)
        self._router = ToolRouter(
            config.SKILL_FILES_URL,
            config.SKILL_RUNNER_URL,
            config.SKILL_WEBSEARCH_URL,
            self._policy,
            self._audit,
            self._tool_registry,
            enable_websearch=enable_websearch,
        )
        self._llm = LLMClient(config.OLLAMA_BASE_URL, config.OLLAMA_MODEL)
        self._context_mgr = ContextManager(
            context_window=config.CONTEXT_WINDOW,
            compact_threshold=config.COMPACT_THRESHOLD,
            llm=self._llm,
        )
        self._prompt_builder = PromptBuilder(
            modules_dir=config.PROMPTS_DIR / "modules",
            legacy_path=config.PROMPTS_DIR / "system.txt",
        )
        self._memory = MemoryManager(self._router, self._llm)
        self._store = ConversationStore(config.DB_PATH)

        self._agent = Agent(
            llm=self._llm,
            router=self._router,
            registry=self._tool_registry,
            audit=self._audit,
            context_mgr=self._context_mgr,
            prompt_builder=self._prompt_builder,
            memory=self._memory,
            tool_tier=config.TOOL_TIER,
        )

        logger.info(
            "AgentApp initialized — model=%s tools=%d tier=%s websearch=%s",
            config.OLLAMA_MODEL,
            len(self._tool_registry.known_tools),
            config.TOOL_TIER,
            enable_websearch,
        )

    def on_mount(self) -> None:
        self.push_screen(
            ChatScreen(
                agent=self._agent,
                store=self._store,
                workspace_path=str(config.WORKSPACE_PATH),
            )
        )

    async def on_unmount(self) -> None:
        await self._router.close()
        await self._llm.close()

    def _setup_logging(self) -> None:
        log_level = os.environ.get("LOG_LEVEL", "WARNING")
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            handlers=[
                logging.FileHandler(
                    config.LOG_PATH.parent / "tui.log" if isinstance(config.LOG_PATH, Path) else "tui.log",
                    encoding="utf-8",
                ),
            ],
        )

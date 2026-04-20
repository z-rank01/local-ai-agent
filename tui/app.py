"""AgentApp — Textual application entry point.

Bootstraps core services and launches the chat screen.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from textual.app import App

from core import config
from core.runtime import RuntimeServices, build_runtime

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

        self._runtime: RuntimeServices = build_runtime()
        self._agent = self._runtime.agent
        self._store = self._runtime.store

        logger.info(
            "AgentApp initialized — model=%s tools=%d tier=%s websearch=%s",
            config.OLLAMA_MODEL,
            len(self._runtime.tool_registry.known_tools),
            config.TOOL_TIER,
            config.ENABLE_WEBSEARCH,
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
        await self._runtime.close()

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

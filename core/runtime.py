"""Shared runtime bootstrap for UI shells and API adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import config
from .agent import Agent
from .audit_logger import AuditLogger
from .context_manager import ContextManager
from .conversation_store import ConversationStore
from .llm_client import LLMClient
from .memory_manager import MemoryManager
from .policy_engine import PolicyEngine
from .prompt_builder import PromptBuilder
from .providers import ModelRegistry
from .stock_bridge import StockBridge
from .tool_registry import ToolRegistry
from .tool_router import ToolRouter


@dataclass(slots=True)
class RuntimeServices:
    tool_registry: ToolRegistry
    policy: PolicyEngine
    audit: AuditLogger
    router: ToolRouter
    llm: LLMClient
    context_mgr: ContextManager
    prompt_builder: PromptBuilder
    memory: MemoryManager
    store: ConversationStore
    agent: Agent
    models: ModelRegistry
    stock_bridge: Any = None

    async def close(self) -> None:
        await self.router.close()
        await self.llm.close()
        await self.models.close()
        if self.stock_bridge is not None:
            await self.stock_bridge.close()

    def agent_for(self, spec, llm):
        cloud = spec['kind'] == 'cloud'
        # Never auto-read shared local memory into a cloud session.
        from copy import copy
        router = copy(self.router)
        router.cloud = cloud
        return Agent(llm=llm, router=router, registry=self.tool_registry,
            audit=self.audit, context_mgr=ContextManager(context_window=config.CONTEXT_WINDOW,
            compact_threshold=config.COMPACT_THRESHOLD, llm=llm), prompt_builder=self.prompt_builder,
            memory=None if cloud else self.memory, tool_tier=config.TOOL_TIER,
            max_rounds=config.AGENT_MAX_ROUNDS)


def build_runtime() -> RuntimeServices:
    """Construct the shared backend runtime used by shells and adapters."""
    enable_websearch = config.ENABLE_WEBSEARCH

    tool_registry = ToolRegistry(
        config.TOOLS_DIR, enable_websearch=enable_websearch,
        stock_bridge_url=config.STOCK_BRIDGE_URL,
    )
    policy = PolicyEngine(config.POLICY_PATH)
    audit = AuditLogger(config.LOG_PATH)
    store = ConversationStore(config.DB_PATH)
    stock_bridge = StockBridge(config.STOCK_BRIDGE_URL, config.STOCK_BRIDGE_TOKEN) if config.STOCK_BRIDGE_URL else None
    router = ToolRouter(
        config.SKILL_FILES_URL,
        config.SKILL_RUNNER_URL,
        config.SKILL_WEBSEARCH_URL,
        policy,
        audit,
        tool_registry,
        store=store,
        enable_websearch=enable_websearch,
        stock_bridge=stock_bridge,
    )
    llm = LLMClient(config.OLLAMA_BASE_URL, config.OLLAMA_MODEL)
    context_mgr = ContextManager(
        context_window=config.CONTEXT_WINDOW,
        compact_threshold=config.COMPACT_THRESHOLD,
        llm=llm,
    )
    prompt_builder = PromptBuilder(
        modules_dir=config.PROMPTS_DIR / "modules",
        legacy_path=config.PROMPTS_DIR / "system.txt",
    )
    memory = MemoryManager(router, llm)
    agent = Agent(
        llm=llm,
        router=router,
        registry=tool_registry,
        audit=audit,
        context_mgr=context_mgr,
        prompt_builder=prompt_builder,
        memory=memory,
        tool_tier=config.TOOL_TIER,
        max_rounds=config.AGENT_MAX_ROUNDS,
    )
    return RuntimeServices(
        tool_registry=tool_registry,
        policy=policy,
        audit=audit,
        router=router,
        llm=llm,
        context_mgr=context_mgr,
        prompt_builder=prompt_builder,
        memory=memory,
        store=store,
        agent=agent,
        models=ModelRegistry(),
        stock_bridge=stock_bridge,
    )

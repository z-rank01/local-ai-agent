"""Shared runtime bootstrap for UI shells and API adapters."""

from __future__ import annotations

from dataclasses import dataclass

from . import config
from .agent import Agent
from .audit_logger import AuditLogger
from .context_manager import ContextManager
from .conversation_store import ConversationStore
from .llm_client import LLMClient
from .memory_manager import MemoryManager
from .policy_engine import PolicyEngine
from .prompt_builder import PromptBuilder
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

    async def close(self) -> None:
        await self.router.close()
        await self.llm.close()


def build_runtime() -> RuntimeServices:
    """Construct the shared backend runtime used by shells and adapters."""
    enable_websearch = config.ENABLE_WEBSEARCH

    tool_registry = ToolRegistry(
        config.TOOLS_DIR, enable_websearch=enable_websearch
    )
    policy = PolicyEngine(config.POLICY_PATH)
    audit = AuditLogger(config.LOG_PATH)
    store = ConversationStore(config.DB_PATH)
    router = ToolRouter(
        config.SKILL_FILES_URL,
        config.SKILL_RUNNER_URL,
        config.SKILL_WEBSEARCH_URL,
        policy,
        audit,
        tool_registry,
        store=store,
        enable_websearch=enable_websearch,
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
    )
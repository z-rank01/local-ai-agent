"""Core library — agentic loop, LLM client, tool routing, memory management.

Extracted from the original gateway/ FastAPI service into a portable Python
package that can be embedded directly in the TUI process.
"""

from .agent import Agent, AgentEvent, MemoryHooks
from .audit_logger import AuditLogger
from . import config
from .context_manager import ContextManager
from .llm_client import LLMClient
from .memory_manager import MemoryManager
from .policy_engine import PolicyEngine
from .prompt_builder import PromptBuilder
from .runtime import RuntimeServices, build_runtime
from .tool_registry import ToolRegistry
from .tool_router import ToolRouter

__all__ = [
    "Agent",
    "AgentEvent",
    "AuditLogger",
    "config",
    "ContextManager",
    "LLMClient",
    "MemoryHooks",
    "MemoryManager",
    "PolicyEngine",
    "PromptBuilder",
    "RuntimeServices",
    "ToolRegistry",
    "ToolRouter",
    "build_runtime",
]

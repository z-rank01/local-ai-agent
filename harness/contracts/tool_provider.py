"""Small execution boundary for trusted providers inside workspace services."""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable
from .models import ModelGateway


@dataclass(frozen=True)
class ExecutionContext:
    workspace_id: str
    workspace_root: Path
    resolve_path: Callable[[str], Path]
    conversation_id: str = ''
    run_id: str = ''
    model: str = ''
    permissions: frozenset[str] = frozenset()
    model_gateway: ModelGateway | None = None


class ToolProvider(Protocol):
    def invoke(self, tool: str, arguments: dict, context: ExecutionContext) -> dict:
        """Return an actual receipt; failures must not be represented as success."""


class ToolTransport(Protocol):
    async def call_tool(self, tool: str, arguments: dict, session_id: str) -> dict:
        """Adapt an installed service; identity and grants remain host-owned."""


@runtime_checkable
class ContextualTransport(Protocol):
    def bind(self, context: ExecutionContext) -> ToolTransport:
        """Return a run-owned binding; do not mutate a shared provider."""

"""Small execution boundary for trusted providers inside workspace services."""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


@dataclass(frozen=True)
class ExecutionContext:
    workspace_id: str
    workspace_root: Path
    resolve_path: Callable[[str], Path]


class ToolProvider(Protocol):
    def invoke(self, tool: str, arguments: dict, context: ExecutionContext) -> dict:
        """Return an actual receipt; failures must not be represented as success."""


class ToolTransport(Protocol):
    async def call_tool(self, tool: str, arguments: dict, session_id: str) -> dict:
        """Adapt an installed service; identity and grants remain host-owned."""

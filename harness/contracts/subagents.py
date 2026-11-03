"""Declarative, one-level subagent contracts with parent-scoped results."""
from dataclasses import dataclass, field
from typing import Any, Protocol


TERMINAL_SUBAGENT_STATUSES = frozenset({'COMPLETE', 'FAILED', 'UNKNOWN', 'TRUNCATED', 'STOPPED'})


@dataclass(frozen=True, slots=True)
class RoleSpec:
    role_id: str
    prompt: str
    tools: frozenset[str] = frozenset()
    depends_on: tuple[str, ...] = ()
    output_artifact_kind: str = 'analysis'
    recipe_namespace: str = 'analysis'
    output_schema: dict[str, Any] = field(default_factory=dict)
    plugin_id: str = ''
    plugin_version: str = ''


@dataclass(frozen=True, slots=True)
class RoleTask:
    role: RoleSpec
    text: str
    evidence_ids: tuple[str, ...] = ()
    batch_id: str = ''


@dataclass(frozen=True, slots=True)
class Artifact:
    kind: str
    content: Any
    producer_role: str
    sha256: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoleResult:
    role_id: str
    status: str
    artifacts: tuple[Artifact, ...] = ()
    issues: tuple[dict[str, Any], ...] = ()
    native_receipt: dict[str, Any] = field(default_factory=dict)
    error: str = ''
    raw_text: str = ''


class SubagentExecutor(Protocol):
    async def run(self, task: RoleTask, context: Any) -> RoleResult:
        """Execute one role without creating another batch or publishing results."""


class SubagentStore(Protocol):
    def create_batch(self, batch_id: str, tasks: tuple[RoleTask, ...]) -> None: ...
    def save_result(self, batch_id: str, result: RoleResult) -> None: ...
    def get_batch(self, batch_id: str) -> dict[str, Any]: ...

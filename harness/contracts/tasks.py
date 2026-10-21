"""Task providers expose authoritative state without copying task databases."""
from typing import Protocol


class TaskProvider(Protocol):
    namespace: str

    async def list(self, workspace_id: str | None) -> dict:
        """Return runs and provider status from the owner's source of truth."""

    async def get(self, run_id: str) -> dict: ...

    async def control(self, run_id: str, action: str) -> dict: ...


class TaskUnavailable(ValueError):
    pass

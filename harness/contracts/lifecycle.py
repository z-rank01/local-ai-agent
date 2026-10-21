"""Public lifecycle contract. Providers own resources; the host owns admission."""
from typing import Protocol


class Lifecycle(Protocol):
    async def activate(self) -> None:
        """Prepare resources and verify identity/health before returning."""

    async def deactivate(self) -> None:
        """Release resources after the host has drained accepted calls."""

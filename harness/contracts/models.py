"""Host-established model request; a capability cannot select a fallback model."""
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelRequest:
    request_id: str
    prompt: str
    material: Any


class ModelGateway(Protocol):
    async def generate(self, request: ModelRequest) -> dict:
        """Return a receipt or reject an already recorded request without resending."""

"""Minimal research session boundary supplied by the host."""
from typing import Protocol


class ResearchSession(Protocol):
    async def begin(self, params: dict) -> dict: ...
    async def publish(self, params: dict) -> dict: ...

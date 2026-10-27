"""Declarative user commands resolved through the normal tool boundary."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Command:
    identity: str
    name: str
    title: str
    tool: str
    parameters: dict
    owners: tuple[str, ...]

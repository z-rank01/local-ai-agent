"""Serializable metadata shared by capability providers and their clients."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    id: str
    version: str
    kind: str
    title: str
    description: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()

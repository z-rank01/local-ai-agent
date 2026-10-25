"""Authorized history projection; source messages are archived before replacement."""
from dataclasses import dataclass, field


@dataclass
class HistoryProjection:
    messages: list
    evidence_ids: set[str] = field(default_factory=set)
    annotations: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)

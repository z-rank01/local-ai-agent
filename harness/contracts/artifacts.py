"""Saved UI-only artifacts and provider-owned remote revisions."""
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SavedArtifact:
    conversation_id: str
    identity: str
    tool: str
    revision: str
    content: str
    metadata: dict[str, Any] = field(repr=False)
    visibility: str = 'conversation-local-ui'
    completeness: str = 'UNKNOWN'
    media_type: str = 'text/plain'

    def public(self):
        return {key: getattr(self, key) for key in
                ('conversation_id', 'identity', 'revision', 'content', 'visibility', 'completeness', 'media_type')}


@dataclass(frozen=True)
class ArtifactPage:
    page: int
    request_id: str
    section: str | None = None
    revision: str | None = None


class ArtifactProvider(Protocol):
    async def read_page(self, saved: SavedArtifact, request: ArtifactPage) -> dict: ...


class ArtifactError(ValueError):
    def __init__(self, status: int, detail: str | dict):
        super().__init__(str(detail))
        self.status, self.detail = status, detail

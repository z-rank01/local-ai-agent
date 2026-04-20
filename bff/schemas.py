"""Pydantic contracts for the frontend adapter API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ConversationSummary(BaseModel):
    id: str
    title: str
    model: str
    created_at: str
    updated_at: str


class MessageRecord(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str = ""
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_name: str = ""
    created_at: str


class ImportedAttachment(BaseModel):
    source_path: str
    local_path: str
    workspace_path: str
    display_name: str


class WorkspaceEntry(BaseModel):
    name: str
    path: str
    kind: Literal["file", "directory"]
    size: int | None = None
    modified_at: str | None = None


class WorkspaceTreeResponse(BaseModel):
    root: str
    entries: list[WorkspaceEntry] = Field(default_factory=list)


class AppStatus(BaseModel):
    status: str = "ok"
    model: str
    workspace_path: str
    tools: list[str]
    websearch_enabled: bool


class CreateConversationRequest(BaseModel):
    title: str = "新对话"
    model: str | None = None


class UpdateConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    title: str | None = None


class WorkspaceImportRequest(BaseModel):
    text: str = Field(min_length=1)


class WorkspaceImportResponse(BaseModel):
    rewritten_text: str
    attachments: list[ImportedAttachment] = Field(default_factory=list)


class UIStreamEvent(BaseModel):
    event: str
    conversation_id: str | None = None
    run_id: str | None = None
    block_id: str | None = None
    message_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
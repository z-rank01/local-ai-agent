"""FastAPI app exposing a stable frontend-facing protocol."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse

from .deps import get_chat_service, get_runtime, shutdown_runtime
from .schemas import (
    AppStatus,
    ChatRequest,
    ConversationSummary,
    CreateConversationRequest,
    MessageRecord,
    UpdateConversationRequest,
    WorkspaceImportRequest,
    WorkspaceImportResponse,
    WorkspaceTreeResponse,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_runtime()
    try:
        yield
    finally:
        await shutdown_runtime()


app = FastAPI(
    title="Local AI Agent Frontend Adapter",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status", response_model=AppStatus)
async def status() -> AppStatus:
    return get_chat_service().app_status()


@app.get("/api/conversations", response_model=list[ConversationSummary])
async def list_conversations(limit: int = 50, offset: int = 0) -> list[ConversationSummary]:
    return get_chat_service().list_conversations(limit=limit, offset=offset)


@app.post("/api/conversations", response_model=ConversationSummary, status_code=201)
async def create_conversation(request: CreateConversationRequest) -> ConversationSummary:
    return get_chat_service().create_conversation(request.title, request.model)


@app.get("/api/conversations/{conversation_id}", response_model=ConversationSummary)
async def get_conversation(conversation_id: str) -> ConversationSummary:
    return get_chat_service().get_conversation(conversation_id)


@app.patch("/api/conversations/{conversation_id}", response_model=ConversationSummary)
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
) -> ConversationSummary:
    return get_chat_service().update_conversation_title(conversation_id, request.title)


@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str) -> Response:
    get_chat_service().delete_conversation(conversation_id)
    return Response(status_code=204)


@app.get("/api/conversations/{conversation_id}/messages", response_model=list[MessageRecord])
async def list_messages(conversation_id: str) -> list[MessageRecord]:
    return get_chat_service().get_messages(conversation_id)


@app.post("/api/workspace/import-local-paths", response_model=WorkspaceImportResponse)
async def import_local_paths(request: WorkspaceImportRequest) -> WorkspaceImportResponse:
    return get_chat_service().import_local_paths(request.text)


@app.get("/api/workspace/tree", response_model=WorkspaceTreeResponse)
async def workspace_tree(path: str = "/workspace") -> WorkspaceTreeResponse:
    return get_chat_service().list_workspace(path)


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    service = get_chat_service()

    async def generate():
        async for event in service.stream_chat(request):
            yield event.model_dump_json() + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")
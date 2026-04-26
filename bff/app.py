"""FastAPI app exposing a stable frontend-facing protocol."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from core import config

from .deps import get_chat_service, get_runtime, shutdown_runtime
from .schemas import (
    ActivateMessageVersionRequest,
    AppStatus,
    ChatRequest,
    ConversationSummary,
    CreateConversationRequest,
    EditMessageRequest,
    MessageRecord,
    ModelInfo,
    ProviderInfo,
    RegenerateRequest,
    UIStreamEvent,
    UpdateConversationRequest,
    WorkspaceFilePreview,
    WorkspaceImportRequest,
    WorkspaceImportResponse,
    WorkspaceTreeResponse,
    WorkspaceUploadResponse,
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.WEB_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _stream_ndjson(
    events: AsyncGenerator[UIStreamEvent, None],
    *,
    conversation_id: str | None,
) -> StreamingResponse:
    first_event: UIStreamEvent | None = None
    try:
        first_event = await anext(events)
    except StopAsyncIteration:
        first_event = None

    async def generate():
        if first_event is not None:
            yield first_event.model_dump_json() + "\n"
        try:
            async for event in events:
                yield event.model_dump_json() + "\n"
        except Exception as exc:
            detail = exc.detail if hasattr(exc, "detail") else str(exc)
            yield UIStreamEvent(
                event="error",
                conversation_id=conversation_id,
                data={"message": str(detail)},
            ).model_dump_json() + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status", response_model=AppStatus)
async def status() -> AppStatus:
    return get_chat_service().app_status()


@app.get("/api/models", response_model=list[ModelInfo])
async def list_models() -> list[ModelInfo]:
    return get_chat_service().list_models()


@app.get("/api/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    return get_chat_service().list_providers()


@app.get("/api/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    query: str | None = None,
) -> list[ConversationSummary]:
    return get_chat_service().list_conversations(limit=limit, offset=offset, query=query)


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


@app.delete("/api/conversations/{conversation_id}/messages/{message_id}", status_code=204)
async def delete_message(conversation_id: str, message_id: str) -> Response:
    get_chat_service().delete_message(conversation_id, message_id)
    return Response(status_code=204)


@app.post("/api/conversations/{conversation_id}/messages/{message_id}/edit")
async def edit_message(
    conversation_id: str,
    message_id: str,
    request: EditMessageRequest,
) -> StreamingResponse:
    service = get_chat_service()
    return await _stream_ndjson(
        service.edit_message_and_regenerate(
            conversation_id,
            message_id=message_id,
            content=request.content,
        ),
        conversation_id=conversation_id,
    )


@app.post("/api/conversations/{conversation_id}/regenerate")
async def regenerate_conversation(
    conversation_id: str, request: RegenerateRequest | None = None
) -> StreamingResponse:
    service = get_chat_service()
    payload = request or RegenerateRequest()
    return await _stream_ndjson(
        service.regenerate_chat(conversation_id, message_id=payload.message_id),
        conversation_id=conversation_id,
    )


@app.get("/api/conversations/{conversation_id}/export")
async def export_conversation(conversation_id: str, format: str = "markdown") -> Response:
    content, filename, media_type = get_chat_service().export_conversation(conversation_id, format=format)
    sanitized = filename.replace('"', '')
    ascii_fallback = sanitized.encode("ascii", errors="ignore").decode("ascii") or "conversation-export"
    encoded = quote(sanitized)
    headers = {
        "content-disposition": (
            f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"
        )
    }
    return Response(content=content, media_type=media_type, headers=headers)


@app.post("/api/workspace/import-local-paths", response_model=WorkspaceImportResponse)
async def import_local_paths(request: WorkspaceImportRequest) -> WorkspaceImportResponse:
    return get_chat_service().import_local_paths(request.text)


@app.get("/api/workspace/tree", response_model=WorkspaceTreeResponse)
async def workspace_tree(path: str = "/workspace") -> WorkspaceTreeResponse:
    return get_chat_service().list_workspace(path)


@app.post("/api/workspace/upload", response_model=WorkspaceUploadResponse, status_code=201)
async def upload_workspace_file(
    request: Request,
    filename: str = Query(min_length=1),
    target_dir: str = "/workspace/data/uploads",
) -> WorkspaceUploadResponse:
    return await get_chat_service().upload_workspace_file(
        filename=filename,
        content_type=request.headers.get("content-type"),
        target_dir=target_dir,
        chunks=request.stream(),
    )


@app.get("/api/workspace/preview", response_model=WorkspaceFilePreview)
async def workspace_file_preview(
    path: str,
    max_bytes: int = Query(200_000, ge=1024, le=1_000_000),
) -> WorkspaceFilePreview:
    return get_chat_service().preview_workspace_file(path, max_bytes=max_bytes)


@app.get("/api/workspace/raw")
async def workspace_file_raw(path: str) -> FileResponse:
    target = get_chat_service().resolve_workspace_file(path)
    return FileResponse(target, filename=target.name)


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    service = get_chat_service()
    return await _stream_ndjson(
        service.stream_chat(request),
        conversation_id=request.conversation_id,
    )


@app.post("/api/conversations/{conversation_id}/messages/{message_id}/activate-version", response_model=list[MessageRecord])
async def activate_message_version(
    conversation_id: str,
    message_id: str,
    request: ActivateMessageVersionRequest,
) -> list[MessageRecord]:
    return get_chat_service().activate_message_version(
        conversation_id,
        message_id=message_id,
        version_number=request.version_number,
    )
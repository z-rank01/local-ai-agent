"""Conversation, workspace, and streaming services for the frontend adapter."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import AsyncGenerator

from fastapi import HTTPException

from core import config
from core.conversation_store import Conversation, ConversationStore, Message
from core.input_utils import ImportedFile, ingest_local_file_paths
from core.runtime import RuntimeServices

from .schemas import (
    AppStatus,
    ChatRequest,
    ConversationSummary,
    ImportedAttachment,
    MessageRecord,
    UIStreamEvent,
    WorkspaceEntry,
    WorkspaceImportResponse,
    WorkspaceTreeResponse,
)


class ChatSessionService:
    """Frontend-facing façade over conversations, workspace, and agent streaming."""

    def __init__(self, runtime: RuntimeServices) -> None:
        self._runtime = runtime
        self._store: ConversationStore = runtime.store
        self._workspace_root = config.WORKSPACE_PATH.resolve()
        self._workspace_root.mkdir(parents=True, exist_ok=True)

    def app_status(self) -> AppStatus:
        return AppStatus(
            model=self._runtime.llm.model,
            workspace_path=str(self._workspace_root),
            tools=sorted(self._runtime.tool_registry.known_tools),
            websearch_enabled=config.ENABLE_WEBSEARCH,
        )

    def list_conversations(self, *, limit: int = 50, offset: int = 0) -> list[ConversationSummary]:
        return [
            self._conversation_summary(conv)
            for conv in self._store.list_conversations(limit=limit, offset=offset)
        ]

    def create_conversation(self, title: str, model: str | None = None) -> ConversationSummary:
        conv = self._store.create_conversation(
            title=title,
            model=model or self._runtime.llm.model,
        )
        return self._conversation_summary(conv)

    def get_conversation(self, conversation_id: str) -> ConversationSummary:
        conv = self._require_conversation(conversation_id)
        return self._conversation_summary(conv)

    def update_conversation_title(self, conversation_id: str, title: str) -> ConversationSummary:
        self._require_conversation(conversation_id)
        self._store.update_conversation_title(conversation_id, title)
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> None:
        self._require_conversation(conversation_id)
        self._store.delete_conversation(conversation_id)

    def get_messages(self, conversation_id: str) -> list[MessageRecord]:
        self._require_conversation(conversation_id)
        return [
            self._message_record(message)
            for message in self._store.get_messages(conversation_id)
        ]

    def import_local_paths(self, text: str) -> WorkspaceImportResponse:
        rewritten_text, imported = ingest_local_file_paths(text, self._workspace_root)
        return WorkspaceImportResponse(
            rewritten_text=rewritten_text,
            attachments=[self._attachment(item) for item in imported],
        )

    def list_workspace(self, requested_path: str = "/workspace") -> WorkspaceTreeResponse:
        target = self._resolve_workspace_path(requested_path)
        entries: list[WorkspaceEntry] = []
        for child in sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            stat = child.stat()
            entries.append(
                WorkspaceEntry(
                    name=child.name,
                    path=self._to_workspace_path(child),
                    kind="directory" if child.is_dir() else "file",
                    size=None if child.is_dir() else stat.st_size,
                    modified_at=str(stat.st_mtime),
                )
            )
        return WorkspaceTreeResponse(root=self._to_workspace_path(target), entries=entries)

    async def stream_chat(self, request: ChatRequest) -> AsyncGenerator[UIStreamEvent, None]:
        message = request.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="message cannot be empty")

        conversation = self._ensure_conversation(request.conversation_id, request.title)
        run_id = uuid.uuid4().hex[:12]
        yield UIStreamEvent(
            event="session.started",
            conversation_id=conversation.id,
            run_id=run_id,
            data={"conversation": self._conversation_summary(conversation).model_dump()},
        )

        import_result = self.import_local_paths(message)
        rewritten_text = import_result.rewritten_text
        if import_result.attachments:
            yield UIStreamEvent(
                event="attachments.imported",
                conversation_id=conversation.id,
                run_id=run_id,
                data=import_result.model_dump(),
            )

        user_message = self._store.add_message(
            conversation.id,
            role="user",
            content=rewritten_text,
        )
        yield UIStreamEvent(
            event="user.accepted",
            conversation_id=conversation.id,
            run_id=run_id,
            message_id=user_message.id,
            data={"content": rewritten_text},
        )

        self._retitle_if_needed(conversation.id, rewritten_text)
        messages = self._store.messages_as_dicts(conversation.id)

        assistant_text = ""
        thinking_text = ""
        assistant_block_id: str | None = None
        reasoning_block_id: str | None = None
        reasoning_open = False
        active_tool_block_id: str | None = None
        active_tool_name: str | None = None

        async for event in self._runtime.agent.run(
            messages,
            session_id=conversation.id,
            conversation_key=conversation.id,
        ):
            if event.kind == "token":
                pending = event.text
                while pending:
                    if reasoning_open:
                        if "</think>" in pending:
                            before, pending = pending.split("</think>", 1)
                            if before:
                                thinking_text += before
                                yield UIStreamEvent(
                                    event="reasoning.delta",
                                    conversation_id=conversation.id,
                                    run_id=run_id,
                                    block_id=reasoning_block_id,
                                    data={"text": before},
                                )
                            yield UIStreamEvent(
                                event="reasoning.completed",
                                conversation_id=conversation.id,
                                run_id=run_id,
                                block_id=reasoning_block_id,
                            )
                            reasoning_open = False
                            reasoning_block_id = None
                            pending = pending.lstrip("\n")
                            continue

                        thinking_text += pending
                        yield UIStreamEvent(
                            event="reasoning.delta",
                            conversation_id=conversation.id,
                            run_id=run_id,
                            block_id=reasoning_block_id,
                            data={"text": pending},
                        )
                        pending = ""
                        continue

                    if "<think>" in pending:
                        before, pending = pending.split("<think>", 1)
                        if before:
                            assistant_block_id = assistant_block_id or uuid.uuid4().hex[:12]
                            assistant_text += before
                            yield UIStreamEvent(
                                event="assistant.delta",
                                conversation_id=conversation.id,
                                run_id=run_id,
                                block_id=assistant_block_id,
                                data={"text": before},
                            )
                        reasoning_block_id = uuid.uuid4().hex[:12]
                        reasoning_open = True
                        yield UIStreamEvent(
                            event="reasoning.started",
                            conversation_id=conversation.id,
                            run_id=run_id,
                            block_id=reasoning_block_id,
                        )
                        pending = pending.lstrip("\n")
                        continue

                    assistant_block_id = assistant_block_id or uuid.uuid4().hex[:12]
                    assistant_text += pending
                    yield UIStreamEvent(
                        event="assistant.delta",
                        conversation_id=conversation.id,
                        run_id=run_id,
                        block_id=assistant_block_id,
                        data={"text": pending},
                    )
                    pending = ""

            elif event.kind == "tool_start":
                active_tool_block_id = uuid.uuid4().hex[:12]
                active_tool_name = event.data.get("name", "tool")
                yield UIStreamEvent(
                    event="tool.started",
                    conversation_id=conversation.id,
                    run_id=run_id,
                    block_id=active_tool_block_id,
                    data={
                        "name": active_tool_name,
                        "summary": event.text,
                        "params": event.data.get("params", {}),
                    },
                )

            elif event.kind == "tool_end":
                tool_name = event.data.get("name") or active_tool_name or "tool"
                status = "ok" if event.data.get("status") == "ok" else "error"
                tool_message = self._store.add_message(
                    conversation.id,
                    role="tool",
                    content=f"[{status}] {event.text}",
                    tool_name=tool_name,
                )
                yield UIStreamEvent(
                    event="tool.completed",
                    conversation_id=conversation.id,
                    run_id=run_id,
                    block_id=active_tool_block_id,
                    message_id=tool_message.id,
                    data={
                        "name": tool_name,
                        "status": status,
                        "detail": event.text,
                        "elapsed": event.data.get("elapsed"),
                    },
                )
                active_tool_block_id = None
                active_tool_name = None

            elif event.kind == "done":
                if event.text and not assistant_text:
                    assistant_block_id = assistant_block_id or uuid.uuid4().hex[:12]
                    assistant_text = event.text
                    yield UIStreamEvent(
                        event="assistant.delta",
                        conversation_id=conversation.id,
                        run_id=run_id,
                        block_id=assistant_block_id,
                        data={"text": event.text},
                    )

                if reasoning_open:
                    yield UIStreamEvent(
                        event="reasoning.completed",
                        conversation_id=conversation.id,
                        run_id=run_id,
                        block_id=reasoning_block_id,
                    )
                    reasoning_open = False

                assistant_message_id: str | None = None
                if assistant_text:
                    assistant_message = self._store.add_message(
                        conversation.id,
                        role="assistant",
                        content=assistant_text,
                        thinking=thinking_text,
                    )
                    assistant_message_id = assistant_message.id

                yield UIStreamEvent(
                    event="assistant.completed",
                    conversation_id=conversation.id,
                    run_id=run_id,
                    block_id=assistant_block_id,
                    message_id=assistant_message_id,
                    data={"text": assistant_text, "thinking": thinking_text},
                )

                latest = self._require_conversation(conversation.id)
                yield UIStreamEvent(
                    event="conversation.updated",
                    conversation_id=conversation.id,
                    run_id=run_id,
                    data={"conversation": self._conversation_summary(latest).model_dump()},
                )
                yield UIStreamEvent(
                    event="session.completed",
                    conversation_id=conversation.id,
                    run_id=run_id,
                )

            elif event.kind == "error":
                yield UIStreamEvent(
                    event="error",
                    conversation_id=conversation.id,
                    run_id=run_id,
                    data={"message": event.text},
                )

    def _ensure_conversation(self, conversation_id: str | None, title: str | None) -> Conversation:
        if conversation_id:
            return self._require_conversation(conversation_id)
        created = self._store.create_conversation(
            title=title or "新对话",
            model=self._runtime.llm.model,
        )
        return self._require_conversation(created.id)

    def _retitle_if_needed(self, conversation_id: str, message: str) -> None:
        conv = self._require_conversation(conversation_id)
        if conv.title != "新对话" or len(conv.messages) > 1:
            return
        self._store.update_conversation_title(conversation_id, message[:40])

    def _require_conversation(self, conversation_id: str) -> Conversation:
        conversation = self._store.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conversation

    def _conversation_summary(self, conversation: Conversation) -> ConversationSummary:
        return ConversationSummary(
            id=conversation.id,
            title=conversation.title,
            model=conversation.model,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )

    def _message_record(self, message: Message) -> MessageRecord:
        tool_calls: list[dict] = []
        if message.tool_calls:
            try:
                tool_calls = json.loads(message.tool_calls)
            except json.JSONDecodeError:
                tool_calls = []
        return MessageRecord(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role,
            content=message.content,
            thinking=message.thinking,
            tool_calls=tool_calls,
            tool_name=message.tool_name,
            created_at=message.created_at,
        )

    def _attachment(self, item: ImportedFile) -> ImportedAttachment:
        return ImportedAttachment(
            source_path=item.source_path,
            local_path=item.local_path,
            workspace_path=item.workspace_path,
            display_name=item.display_name,
        )

    def _resolve_workspace_path(self, requested_path: str) -> Path:
        normalized = (requested_path or "/workspace").replace("\\", "/")
        if normalized in {"", "/", "/workspace"}:
            return self._workspace_root
        if normalized.startswith("/workspace/"):
            relative = normalized[len("/workspace/") :]
        elif normalized.startswith("/"):
            relative = normalized[1:]
        else:
            relative = normalized

        candidate = (self._workspace_root / relative).resolve()
        if candidate != self._workspace_root and self._workspace_root not in candidate.parents:
            raise HTTPException(status_code=403, detail="workspace path escapes root")
        if not candidate.exists() or not candidate.is_dir():
            raise HTTPException(status_code=404, detail="workspace path not found")
        return candidate

    def _to_workspace_path(self, target: Path) -> str:
        if target == self._workspace_root:
            return "/workspace"
        return f"/workspace/{target.relative_to(self._workspace_root).as_posix()}"
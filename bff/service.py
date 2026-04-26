"""Conversation, workspace, and streaming services for the frontend adapter."""

from __future__ import annotations

import json
import mimetypes
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, AsyncIterable

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
    ModelInfo,
    ProviderInfo,
    UIStreamEvent,
    WorkspaceEntry,
    WorkspaceFilePreview,
    WorkspaceImportResponse,
    WorkspaceTreeResponse,
    WorkspaceUploadResponse,
)


_PREVIEW_ENCODINGS = ("utf-8", "gbk", "gb2312", "gb18030", "big5", "latin-1")


class ChatSessionService:
    """Frontend-facing façade over conversations, workspace, and agent streaming."""

    def __init__(self, runtime: RuntimeServices) -> None:
        self._runtime = runtime
        self._store: ConversationStore = runtime.store
        self._workspace_root = config.WORKSPACE_PATH.resolve()
        self._workspace_root.mkdir(parents=True, exist_ok=True)

    def app_status(self) -> AppStatus:
        tools = sorted(self._runtime.tool_registry.known_tools)
        return AppStatus(
            model=self._runtime.llm.model,
            workspace_path=str(self._workspace_root),
            tools=tools,
            websearch_enabled=config.ENABLE_WEBSEARCH and "web_search" in tools,
        )

    def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=f"ollama:{self._runtime.llm.model}",
                name=self._runtime.llm.model,
                provider_id="ollama",
                provider_name="Ollama",
                default=True,
                capabilities=["text", "tools", "streaming", "reasoning"],
                context_window=config.CONTEXT_WINDOW,
                status="available",
            )
        ]

    def list_providers(self) -> list[ProviderInfo]:
        return [
            ProviderInfo(
                id="ollama",
                name="Ollama",
                kind="local",
                enabled=True,
                base_url=config.OLLAMA_BASE_URL,
                models=self.list_models(),
            )
        ]

    def list_conversations(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        query: str | None = None,
    ) -> list[ConversationSummary]:
        return [
            self._conversation_summary(conv)
            for conv in self._store.list_conversations(limit=limit, offset=offset, query=query)
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
            entries.append(self._workspace_entry(child))
        return WorkspaceTreeResponse(root=self._to_workspace_path(target), entries=entries)

    async def upload_workspace_file(
        self,
        *,
        filename: str,
        content_type: str | None,
        target_dir: str,
        chunks: AsyncIterable[bytes],
    ) -> WorkspaceUploadResponse:
        target_directory = self._resolve_workspace_directory(target_dir, create=True)
        target = self._deduplicate_file_path(target_directory / self._safe_filename(filename))

        size = 0
        with target.open("wb") as handle:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                handle.write(chunk)

        workspace_path = self._to_workspace_path(target)
        attachment = ImportedAttachment(
            source_path=f"browser-upload:{filename}",
            local_path=str(target),
            workspace_path=workspace_path,
            display_name=target.name,
        )
        entry = self._workspace_entry(target, size=size, mime_type=content_type)
        return WorkspaceUploadResponse(attachment=attachment, entry=entry)

    def preview_workspace_file(self, requested_path: str, max_bytes: int = 200_000) -> WorkspaceFilePreview:
        max_bytes = max(1024, min(max_bytes, 1_000_000))
        target = self.resolve_workspace_file(requested_path)
        stat = target.stat()
        mime_type = self._guess_mime_type(target)
        with target.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        payload = raw[:max_bytes]
        truncated = stat.st_size > max_bytes or len(raw) > max_bytes
        is_binary = self._looks_binary(payload)
        if mime_type and (mime_type.startswith("image/") or mime_type in {"application/pdf", "application/zip"}):
            is_binary = True

        content: str | None = None
        encoding: str | None = None
        if not is_binary:
            for candidate in _PREVIEW_ENCODINGS:
                try:
                    content = payload.decode(candidate)
                    encoding = candidate
                    break
                except UnicodeDecodeError:
                    continue
            if content is None:
                content = payload.decode("utf-8", errors="replace")
                encoding = "utf-8+replace"

        return WorkspaceFilePreview(
            name=target.name,
            path=self._to_workspace_path(target),
            size=stat.st_size,
            modified_at=self._format_mtime(stat.st_mtime),
            mime_type=mime_type,
            encoding=encoding,
            content=content,
            is_binary=is_binary,
            truncated=truncated,
            max_bytes=max_bytes,
        )

    def resolve_workspace_file(self, requested_path: str) -> Path:
        target = self._resolve_workspace_target(requested_path)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="workspace file not found")
        return target

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
        async for event in self._run_assistant_turn(conversation, run_id):
            yield event

    async def regenerate_chat(
        self, conversation_id: str, *, message_id: str | None = None
    ) -> AsyncGenerator[UIStreamEvent, None]:
        conversation = self._require_conversation(conversation_id)
        messages = self._store.get_messages(conversation_id)

        # Locate the user message we will regenerate from. Default: most recent.
        target: Message | None = None
        if message_id:
            for item in messages:
                if item.id == message_id:
                    if item.role != "user":
                        raise HTTPException(status_code=422, detail="regenerate target must be a user message")
                    target = item
                    break
            if target is None:
                raise HTTPException(status_code=404, detail="message not found")
        else:
            for item in reversed(messages):
                if item.role == "user":
                    target = item
                    break
        if target is None:
            raise HTTPException(status_code=422, detail="no user message to regenerate from")

        # Drop everything created strictly after the target user message.
        self._store.delete_messages_from(conversation_id, target.id, inclusive=False)
        conversation = self._require_conversation(conversation_id)

        run_id = uuid.uuid4().hex[:12]
        yield UIStreamEvent(
            event="session.started",
            conversation_id=conversation.id,
            run_id=run_id,
            data={
                "conversation": self._conversation_summary(conversation).model_dump(),
                "regenerated_from": target.id,
            },
        )
        yield UIStreamEvent(
            event="user.accepted",
            conversation_id=conversation.id,
            run_id=run_id,
            message_id=target.id,
            data={"content": target.content, "regenerated": True},
        )
        async for event in self._run_assistant_turn(conversation, run_id):
            yield event

    async def edit_message_and_regenerate(
        self,
        conversation_id: str,
        *,
        message_id: str,
        content: str,
    ) -> AsyncGenerator[UIStreamEvent, None]:
        conversation = self._require_conversation(conversation_id)
        target = self._store.get_message(message_id)
        if target is None or target.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="message not found")
        if target.role != "user":
            raise HTTPException(status_code=422, detail="only user messages can be edited")

        updated_text = content.strip()
        if not updated_text:
            raise HTTPException(status_code=422, detail="message cannot be empty")

        import_result = self.import_local_paths(updated_text)
        rewritten_text = import_result.rewritten_text
        if not self._store.update_message_content(conversation_id, message_id, rewritten_text):
            raise HTTPException(status_code=404, detail="message not found")
        self._store.delete_messages_from(conversation_id, message_id, inclusive=False)
        conversation = self._require_conversation(conversation_id)

        run_id = uuid.uuid4().hex[:12]
        yield UIStreamEvent(
            event="session.started",
            conversation_id=conversation.id,
            run_id=run_id,
            data={
                "conversation": self._conversation_summary(conversation).model_dump(),
                "edited_message_id": message_id,
            },
        )
        if import_result.attachments:
            yield UIStreamEvent(
                event="attachments.imported",
                conversation_id=conversation.id,
                run_id=run_id,
                data=import_result.model_dump(),
            )
        yield UIStreamEvent(
            event="user.accepted",
            conversation_id=conversation.id,
            run_id=run_id,
            message_id=message_id,
            data={"content": rewritten_text, "edited": True},
        )
        async for event in self._run_assistant_turn(conversation, run_id):
            yield event

    def delete_message(self, conversation_id: str, message_id: str) -> None:
        self._require_conversation(conversation_id)
        if not self._store.delete_message(conversation_id, message_id):
            raise HTTPException(status_code=404, detail="message not found")

    def export_conversation(self, conversation_id: str, format: str = "markdown") -> tuple[str, str, str]:
        normalized = (format or "markdown").strip().lower()
        if normalized == "markdown":
            content, filename = self.export_conversation_markdown(conversation_id)
            return content, filename, "text/markdown; charset=utf-8"
        if normalized == "json":
            content, filename = self.export_conversation_json(conversation_id)
            return content, filename, "application/json; charset=utf-8"
        if normalized in {"txt", "text", "plain"}:
            content, filename = self.export_conversation_text(conversation_id)
            return content, filename, "text/plain; charset=utf-8"
        raise HTTPException(status_code=400, detail=f"unsupported format: {format}")

    def export_conversation_markdown(self, conversation_id: str) -> tuple[str, str]:
        conversation = self._require_conversation(conversation_id)
        lines: list[str] = [
            f"# {conversation.title}",
            "",
            f"- 会话 ID：`{conversation.id}`",
            f"- 模型：`{conversation.model or 'default'}`",
            f"- 创建于：{conversation.created_at}",
            f"- 更新于：{conversation.updated_at}",
            "",
            "---",
            "",
        ]
        role_labels = {
            "user": "🧑 用户",
            "assistant": "🤖 助手",
            "tool": "🛠 工具",
            "system": "⚙️ 系统",
        }
        for message in conversation.messages:
            label = role_labels.get(message.role, message.role)
            lines.append(f"## {label} · {message.created_at}")
            lines.append("")
            if message.role == "tool":
                lines.append(f"**工具**：`{message.tool_name or 'tool'}`")
                lines.append("")
                lines.append("```text")
                lines.append(message.content)
                lines.append("```")
            else:
                if message.thinking:
                    lines.append("<details><summary>思考过程</summary>")
                    lines.append("")
                    lines.append("```text")
                    lines.append(message.thinking)
                    lines.append("```")
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")
                lines.append(message.content or "_(空)_")
            lines.append("")
        markdown = "\n".join(lines).rstrip() + "\n"
        safe_title = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", conversation.title).strip(" .") or "conversation"
        filename = f"{safe_title}-{conversation.id}.md"
        return markdown, filename

    def export_conversation_json(self, conversation_id: str) -> tuple[str, str]:
        conversation = self._require_conversation(conversation_id)
        payload = {
            "conversation": {
                "id": conversation.id,
                "title": conversation.title,
                "model": conversation.model,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            },
            "messages": [self._export_message_payload(message) for message in conversation.messages],
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        safe_title = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", conversation.title).strip(" .") or "conversation"
        filename = f"{safe_title}-{conversation.id}.json"
        return content, filename

    def export_conversation_text(self, conversation_id: str) -> tuple[str, str]:
        conversation = self._require_conversation(conversation_id)
        lines: list[str] = [
            f"标题: {conversation.title}",
            f"会话 ID: {conversation.id}",
            f"模型: {conversation.model or 'default'}",
            f"创建于: {conversation.created_at}",
            f"更新于: {conversation.updated_at}",
            "",
            "=" * 72,
            "",
        ]
        role_labels = {
            "user": "用户",
            "assistant": "助手",
            "tool": "工具",
            "system": "系统",
        }
        for message in conversation.messages:
            label = role_labels.get(message.role, message.role)
            lines.append(f"[{label}] {message.created_at}")
            if message.tool_name:
                lines.append(f"工具名: {message.tool_name}")
            if message.thinking:
                lines.append("思考过程:")
                lines.append(message.thinking)
                lines.append("")
            lines.append(message.content or "(空)")
            lines.append("")
            lines.append("-" * 72)
            lines.append("")
        content = "\n".join(lines).rstrip() + "\n"
        safe_title = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", conversation.title).strip(" .") or "conversation"
        filename = f"{safe_title}-{conversation.id}.txt"
        return content, filename

    async def _run_assistant_turn(
        self, conversation: Conversation, run_id: str
    ) -> AsyncGenerator[UIStreamEvent, None]:
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
                detail = str(event.data.get("result_preview") or event.text)
                headline = str(event.text)
                tool_content = f"[{status}] {headline}"
                if detail and detail != headline:
                    tool_content = f"{tool_content}\n{detail}"
                tool_message = self._store.add_message(
                    conversation.id,
                    role="tool",
                    content=tool_content,
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
                        "headline": headline,
                        "detail": detail,
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

    @staticmethod
    def _export_message_payload(message: Message) -> dict[str, object]:
        tool_calls: object = []
        if message.tool_calls:
            try:
                tool_calls = json.loads(message.tool_calls)
            except json.JSONDecodeError:
                tool_calls = message.tool_calls
        return {
            "id": message.id,
            "conversation_id": message.conversation_id,
            "role": message.role,
            "content": message.content,
            "thinking": message.thinking,
            "tool_name": message.tool_name,
            "tool_calls": tool_calls,
            "created_at": message.created_at,
        }

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
        return self._resolve_workspace_directory(requested_path)

    def _resolve_workspace_directory(self, requested_path: str, *, create: bool = False) -> Path:
        target = self._resolve_workspace_target(requested_path)
        if create and not target.exists():
            target.mkdir(parents=True, exist_ok=True)
        if not target.exists() or not target.is_dir():
            raise HTTPException(status_code=404, detail="workspace path not found")
        return target

    def _resolve_workspace_target(self, requested_path: str) -> Path:
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
        return candidate

    def _to_workspace_path(self, target: Path) -> str:
        if target == self._workspace_root:
            return "/workspace"
        return f"/workspace/{target.relative_to(self._workspace_root).as_posix()}"

    def _workspace_entry(
        self,
        target: Path,
        *,
        size: int | None = None,
        mime_type: str | None = None,
    ) -> WorkspaceEntry:
        stat = target.stat()
        is_directory = target.is_dir()
        return WorkspaceEntry(
            name=target.name,
            path=self._to_workspace_path(target),
            kind="directory" if is_directory else "file",
            size=None if is_directory else size if size is not None else stat.st_size,
            modified_at=self._format_mtime(stat.st_mtime),
            mime_type=None if is_directory else mime_type or self._guess_mime_type(target),
        )

    @staticmethod
    def _format_mtime(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

    @staticmethod
    def _safe_filename(filename: str) -> str:
        safe = Path(filename or "upload.bin").name.strip()
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", safe).strip(" .")
        return safe or "upload.bin"

    @staticmethod
    def _deduplicate_file_path(target: Path) -> Path:
        if not target.exists():
            return target
        stem = target.stem or "upload"
        suffix = target.suffix
        index = 1
        while True:
            candidate = target.with_name(f"{stem}_{index}{suffix}")
            if not candidate.exists():
                return candidate
            index += 1

    @staticmethod
    def _guess_mime_type(target: Path) -> str | None:
        return mimetypes.guess_type(target.name)[0]

    @staticmethod
    def _looks_binary(data: bytes) -> bool:
        if not data:
            return False
        if b"\x00" in data:
            return True
        sample = data[:8192]
        control = sum(1 for byte in sample if byte < 8 or 14 <= byte < 32)
        return control / len(sample) > 0.10
"""Conversation, workspace, and streaming services for the frontend adapter."""

from __future__ import annotations

import asyncio
from contextlib import aclosing

import json
import mimetypes
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, AsyncIterable

import httpx
from fastapi import HTTPException

from core import config, model_settings
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
    WorkspaceDeleteResponse,
    WorkspaceFilePreview,
    WorkspaceImportResponse,
    WorkspaceRestoreResponse,
    WorkspaceTrashEntry,
    WorkspaceTrashResponse,
    WorkspaceTreeResponse,
    WorkspaceUploadResponse,
)


_PREVIEW_ENCODINGS = ("utf-8", "gbk", "gb2312", "gb18030", "big5", "latin-1")

_SECRET_PARAM_KEY = re.compile(r"key|token|secret|password|passwd|credential|authorization|cookie", re.IGNORECASE)


def _redact_params(value):
    """Mask secret-looking parameter values in display copies.

    Execution and protocol replay keep the raw arguments the model produced;
    only UI events and tool row metadata are redacted. The audit log keeps raw
    values as the local forensic record.
    """
    if isinstance(value, dict):
        return {k: "***" if _SECRET_PARAM_KEY.search(str(k)) else _redact_params(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_params(item) for item in value]
    return value


def exclusive_turn(method):
    async def guarded(self, *args, **kwargs):
        key = args[0].conversation_id if isinstance(args[0], ChatRequest) else args[0]
        key = key or '__new__'
        if key in self._active_conversations:
            raise HTTPException(409, '本会话仍在运行，请等待或停止后再发送。')
        self._active_conversations.add(key)
        keys = {key}
        try:
            async with aclosing(method(self, *args, **kwargs)) as events:
                async for event in events:
                    if event.conversation_id:
                        keys.add(event.conversation_id)
                        self._active_conversations.add(event.conversation_id)
                    yield event
        finally:
            self._active_conversations.difference_update(keys)
    return guarded


class ChatSessionService:
    """Frontend-facing façade over conversations, workspace, and agent streaming."""

    def __init__(self, runtime: RuntimeServices) -> None:
        self._runtime = runtime
        self._active_conversations = set()
        self._store: ConversationStore = runtime.store
        self._workspace_root = config.WORKSPACE_PATH.resolve()
        self._workspace_root.mkdir(parents=True, exist_ok=True)

    def app_status(self) -> AppStatus:
        tools = sorted(self._runtime.tool_registry.known_tools)
        budget = self._runtime.models.budget.status()
        return AppStatus(
            model_calls_used=budget["used"], model_call_limit=budget["limit"],
            workspace_cloud_allowed=model_settings.workspace_allowed(),
            model=self._runtime.models.default,
            workspace_path=str(self._workspace_root),
            tools=tools,
            websearch_enabled=config.ENABLE_WEBSEARCH and "web_search" in tools,
        )

    def list_models(self) -> list[ModelInfo]:
        from core.providers import read_key
        return [ModelInfo(id=s['id'], name=s['model'], provider_id=s['provider_id'],
            provider_name=s['provider_name'], default=s['id'] == self._runtime.models.default,
            thinking_supported='enable_thinking' in s.get('options', {}),
            thinking_enabled=model_settings.thinking_enabled(s),
            capabilities=['text','tools','streaming'], context_window=config.CONTEXT_WINDOW,
            status='configured' if s['kind']=='local' or read_key(s.get('api_key_env','')) else 'missing_key')
            for s in self._runtime.models.specs]

    def list_providers(self) -> list[ProviderInfo]:
        models = self.list_models()
        return [ProviderInfo(id=s['provider_id'], name=s['provider_name'], kind=s['kind'],
            base_url=s['base_url'], models=[m for m in models if m.provider_id == s['provider_id']])
            for s in {s['provider_id']:s for s in self._runtime.models.specs}.values()]

    def _select_model(self, conversation, provider=None, model=None):
        try:
            spec, client = self._runtime.models.resolve(provider, model, fallback=conversation.model)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        self._store.set_model(conversation.id, spec['id'])
        conversation.model = spec['id']
        return spec, client

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
            model=model or self._runtime.models.default,
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
        version_counts = self._response_version_counts(conversation_id)
        return [
            self._message_record(message, version_count=version_counts.get(message.response_to_message_id, 1))
            for message in self._store.get_messages(conversation_id) if message.role != "protocol"
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

    async def delete_workspace_file(self, requested_path: str) -> WorkspaceDeleteResponse:
        target = self.resolve_workspace_file(requested_path)
        workspace_path = self._to_workspace_path(target)

        try:
            result = await self._runtime.router.dispatch(
                "file_delete",
                {"path": workspace_path},
                session_id="workspace-ui",
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        moved_to_trash = result.get("moved_to_trash") if isinstance(result, dict) else None
        if not isinstance(moved_to_trash, str) or not moved_to_trash:
            raise HTTPException(status_code=502, detail="workspace delete returned no trash path")

        operation_id = result.get("operation_id") if isinstance(result, dict) else None
        manifest = result.get("manifest") if isinstance(result, dict) else None
        return WorkspaceDeleteResponse(
            deleted_path=workspace_path,
            moved_to_trash=moved_to_trash,
            operation_id=operation_id if isinstance(operation_id, str) else None,
            manifest=manifest if isinstance(manifest, str) else None,
        )

    async def list_workspace_trash(self) -> WorkspaceTrashResponse:
        payload = await self._call_skill_files("GET", "/trash/items")
        raw_items = payload.get("items") if isinstance(payload, dict) else []
        items: list[WorkspaceTrashEntry] = []
        if isinstance(raw_items, list):
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue
                item_type = "directory" if str(raw_item.get("item_type")) == "directory" else "file"
                items.append(
                    WorkspaceTrashEntry(
                        operation_id=str(raw_item.get("operation_id") or ""),
                        deleted_at=str(raw_item.get("deleted_at") or "") or None,
                        name=str(raw_item.get("name") or ""),
                        relative_path=str(raw_item.get("relative_path") or ""),
                        workspace_path=str(raw_item.get("workspace_path") or ""),
                        original_path=str(raw_item.get("original_path") or ""),
                        trash_path=str(raw_item.get("trash_path") or ""),
                        item_type=item_type,
                        exists_in_trash=bool(raw_item.get("exists_in_trash", False)),
                    )
                )
        return WorkspaceTrashResponse(items=items)

    async def restore_workspace_trash_item(self, operation_id: str) -> WorkspaceRestoreResponse:
        payload = await self._call_skill_files(
            "POST",
            "/trash/restore",
            json_body={"operation_id": operation_id},
        )
        return WorkspaceRestoreResponse(
            operation_id=str(payload.get("operation_id") or operation_id),
            restored_to=str(payload.get("restored_to") or ""),
            workspace_path=str(payload.get("workspace_path") or ""),
            deleted_at=str(payload.get("deleted_at") or "") or None,
        )

    @exclusive_turn
    async def stream_chat(self, request: ChatRequest) -> AsyncGenerator[UIStreamEvent, None]:
        if request.request_id and not self._store.claim_chat_request(request.request_id):
            raise HTTPException(409, '该请求已受理，请刷新查看已有结果；如需再次执行请发送新消息。')
        message = request.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="message cannot be empty")

        conversation = self._ensure_conversation(request.conversation_id, request.title)
        self._select_model(conversation, request.provider_id, request.model)
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
            metadata={"cloud_safe": True},
        )
        yield UIStreamEvent(
            event="user.accepted",
            conversation_id=conversation.id,
            run_id=run_id,
            message_id=user_message.id,
            data={"content": rewritten_text},
        )

        self._retitle_if_needed(conversation.id, rewritten_text)
        async with aclosing(self._run_assistant_turn(
            conversation,
            run_id,
            response_to_message_id=user_message.id,
            response_version_number=1,
        )) as events:
            async for event in events:
                yield event

    @exclusive_turn
    async def regenerate_chat(
        self, conversation_id: str, *, message_id: str | None = None, provider_id=None, model=None, request_id: str | None = None
    ) -> AsyncGenerator[UIStreamEvent, None]:
        if request_id and not self._store.claim_chat_request(request_id):
            raise HTTPException(409, '该请求已受理，请刷新查看已有结果；如需再次执行请发送新消息。')
        conversation = self._require_conversation(conversation_id)
        self._select_model(conversation, provider_id, model)
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

        source_rows = [m for m in messages if m.response_to_message_id == target.id]
        # Regeneration reuses observations; it is never an implicit rerun of writes or scripts.
        last_tool = max((i for i,m in enumerate(source_rows) if m.role == 'protocol' and json.loads(m.metadata or '{}').get('message',{}).get('role') == 'tool'), default=-1)
        reusable = source_rows[:last_tool + 2] if last_tool >= 0 else []
        latest_user = self._store.find_last_user_message(conversation_id)
        preserve_versions = latest_user is not None and latest_user.id == target.id
        response_version_number = 1
        if preserve_versions:
            response_version_number = self._store.next_response_version_number(conversation_id, target.id)
            self._store.deactivate_response_versions(conversation_id, target.id)
        else:
            # Earlier-turn regenerate still rewrites later history to avoid branching the conversation tree.
            self._store.delete_messages_from(conversation_id, target.id, inclusive=False)
        for row in reusable:
            self._store.add_message(conversation_id, role=row.role, content=row.content,
                thinking=row.thinking, tool_name=row.tool_name,
                tool_result=json.loads(row.tool_result) if row.tool_result else None,
                metadata=json.loads(row.metadata or '{}'), response_to_message_id=target.id,
                version_number=response_version_number)
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
        async with aclosing(self._run_assistant_turn(
            conversation,
            run_id,
            response_to_message_id=target.id,
            response_version_number=response_version_number,
            answer_only=True,
        )) as events:
            async for event in events:
                yield event

    @exclusive_turn
    async def edit_message_and_regenerate(
        self,
        conversation_id: str,
        *,
        message_id: str,
        content: str,
        provider_id=None, model=None, request_id: str | None = None,
    ) -> AsyncGenerator[UIStreamEvent, None]:
        if request_id and not self._store.claim_chat_request(request_id):
            raise HTTPException(409, '该请求已受理，请刷新查看已有结果；如需再次执行请发送新消息。')
        conversation = self._require_conversation(conversation_id)
        self._select_model(conversation, provider_id, model)
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
        async with aclosing(self._run_assistant_turn(
            conversation,
            run_id,
            response_to_message_id=message_id,
            response_version_number=1,
        )) as events:
            async for event in events:
                yield event

    def activate_message_version(
        self,
        conversation_id: str,
        *,
        message_id: str,
        version_number: int,
    ) -> list[MessageRecord]:
        self._require_conversation(conversation_id)
        target = self._store.get_message(message_id)
        if target is None or target.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="message not found")
        if target.role != "assistant":
            raise HTTPException(status_code=422, detail="only assistant messages support version switching")
        response_to_message_id = target.response_to_message_id
        if not response_to_message_id:
            raise HTTPException(status_code=422, detail="message has no alternate versions")
        if version_number < 1:
            raise HTTPException(status_code=422, detail="version_number must be >= 1")
        if not self._store.set_response_version_active(conversation_id, response_to_message_id, version_number):
            raise HTTPException(status_code=404, detail="version not found")
        return self.get_messages(conversation_id)

    def delete_message(self, conversation_id: str, message_id: str) -> None:
        self._require_conversation(conversation_id)
        if conversation_id in self._active_conversations:
            raise HTTPException(409, '本会话仍在运行')
        message = self._store.get_message(message_id)
        if not message or message.conversation_id != conversation_id:
            raise HTTPException(404, 'message not found')
        response_id = message.id if message.role == 'user' else message.response_to_message_id
        if response_id:
            self._store.delete_response_versions(conversation_id, response_id)
        if message.role == 'user' or not response_id:
            self._store.delete_message(conversation_id, message_id)

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
                status, headline, detail = self._tool_message_parts(message)
                lines.append(f"**工具**：`{message.tool_name or 'tool'}`")
                lines.append(f"**状态**：{status}")
                if headline:
                    lines.append(f"**摘要**：{headline}")
                lines.append("")
                tool_result = self._parse_tool_result(message)
                if tool_result is not None:
                    lines.append("<details><summary>结构化结果</summary>")
                    lines.append("")
                    lines.append("```json")
                    lines.append(json.dumps(tool_result, ensure_ascii=False, indent=2))
                    lines.append("```")
                    lines.append("")
                    lines.append("</details>")
                elif detail:
                    lines.append("```text")
                    lines.append(detail)
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
            if message.role == "tool":
                status, headline, detail = self._tool_message_parts(message)
                lines.append(f"状态: {status}")
                if headline:
                    lines.append(f"摘要: {headline}")
                tool_result = self._parse_tool_result(message)
                if tool_result is not None:
                    lines.append("结构化结果:")
                    lines.append(json.dumps(tool_result, ensure_ascii=False, indent=2))
                elif detail:
                    lines.append("结果详情:")
                    lines.append(detail)
                lines.append("")
                lines.append("-" * 72)
                lines.append("")
                continue
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

    async def _run_assistant_turn(self, conversation, run_id, *, response_to_message_id, response_version_number, answer_only=False):
        spec, llm = self._select_model(conversation)
        cloud = spec['kind'] == 'cloud'
        workspace_allowed = model_settings.workspace_allowed()
        if 'enable_thinking' in spec.get('options', {}):
            import copy
            llm = copy.copy(llm)
            llm.spec = {**spec, 'options': {**spec['options'], 'enable_thinking': model_settings.thinking_enabled(spec)}}
        messages = self._store.messages_as_dicts(conversation.id, cloud=cloud)
        agent = self._runtime.agent_for(spec, llm)
        agent.allow_tools = not answer_only
        agent.workspace_cloud_allowed = workspace_allowed
        base = dict(conversation_id=conversation.id, run_id=run_id)
        meta = {'model': spec['id'], 'cloud_safe': cloud or workspace_allowed}
        response_meta = dict(response_to_message_id=response_to_message_id, version_number=response_version_number)
        text, reasoning, block, thinking_block = '', '', uuid.uuid4().hex, uuid.uuid4().hex
        thinking_open = False
        active_tools = {}
        def emit(event, **kwargs):
            return UIStreamEvent(event=event, **base, **kwargs)
        def save(role, content='', **kwargs):
            return self._store.add_message(conversation.id, role=role, content=content, **response_meta, **kwargs)
        try:
            yield emit('model.selected', data={'model': spec['id'], 'cloud': cloud,
                'workspace_cloud_allowed': workspace_allowed})
            async with aclosing(agent.run(messages, conversation.id, conversation.id)) as events:
                async for event in events:
                    if event.kind == 'token':
                        # Model transports emit reasoning delimiters as complete protocol tokens.
                        token = event.text
                        if token.strip() == '<think>':
                            thinking_open = True
                            yield emit('reasoning.started', block_id=thinking_block)
                        elif token.strip() == '</think>':
                            thinking_open = False
                            yield emit('reasoning.completed', block_id=thinking_block)
                        elif thinking_open:
                            reasoning += token
                            yield emit('reasoning.delta', block_id=thinking_block, data={'text': token})
                        else:
                            text += token
                            yield emit('assistant.delta', block_id=block, data={'text': token, 'model': spec['id']})
                    elif event.kind == 'message':
                        message = event.data['message']
                        save('protocol', metadata={**meta, 'message': message})
                        if message['role'] == 'assistant':
                            final_text = message.get('content') or text
                            if final_text or reasoning:
                                saved = save('assistant', final_text, thinking=reasoning, metadata=meta)
                                yield emit('assistant.completed', block_id=block, message_id=saved.id,
                                    data={'text': final_text, 'model': spec['id']})
                            text, reasoning = '', ''
                            block, thinking_block = uuid.uuid4().hex, uuid.uuid4().hex
                    elif event.kind == 'tool_start':
                        cid = event.data['call_id']
                        active_tools[cid] = event.data
                        yield emit('tool.started', block_id=cid, data={**event.data,
                            'params': _redact_params(event.data.get('params', {})), 'summary': event.data['name']})
                    elif event.kind == 'tool_progress':
                        running = active_tools.get(event.data['call_id'])
                        if running is not None and event.data.get('event') == 'output':
                            stream_name = event.data.get('stream','stdout')
                            partial = running.setdefault('partial', {})
                            partial[stream_name] = (partial.get(stream_name,'') + event.data.get('text',''))[:51200]
                        yield emit('tool.progress', block_id=event.data['call_id'], data=event.data)
                    elif event.kind == 'tool_end':
                        data = event.data
                        display_params = _redact_params(data.get('params', {}))
                        saved = save('tool', '[' + data['status'] + '] ' + data['name'] + (' 已完成' if data['status']=='ok' else ' 执行失败') + '\n' + data.get('result_preview',''), tool_name=data['name'],
                            tool_result=data.get('result'), metadata={**meta, 'params': display_params, 'status': data['status']})
                        active_tools.pop(data['call_id'], None)
                        yield emit('tool.completed', block_id=data['call_id'], message_id=saved.id,
                            data={**data, 'params': display_params, 'detail': data.get('result_preview',''), 'headline': event.text})
                    elif event.kind == 'error':
                        partial = text + ('\n\n' if text else '') + '本轮未完成：' + event.text
                        saved = save('assistant', partial, thinking=reasoning, metadata={**meta, 'status':'error'})
                        text = ''
                        yield emit('assistant.completed', block_id=block, message_id=saved.id,
                            data={'text': partial, 'model':spec['id'], 'status':'error'})
                        yield emit('error', data={'message': event.text})
            latest = self._require_conversation(conversation.id)
            yield emit('conversation.updated', data={'conversation':self._conversation_summary(latest).model_dump()})
            yield emit('session.completed')
        finally:
            # Disconnects/stop preserve partial output and terminal tool states.
            if text or reasoning:
                save('assistant', text + '\n\n[生成已中断]', thinking=reasoning, metadata={**meta,'status':'interrupted'})
            for tool in active_tools.values():
                result = {**tool.get('partial', {}), 'error':'执行已中断，请核查已有结果；不会自动重跑。'}
                save('protocol', metadata={**meta, 'message':{'role':'tool', 'tool_call_id':tool['call_id'],
                    'tool_name':tool['name'], 'content':json.dumps(result,ensure_ascii=False)}})
                save('tool', '[error] 执行已中断，请核查已有结果。', tool_name=tool['name'],
                    tool_result=result, metadata={**meta,'status':'error','params':_redact_params(tool.get('params', {}))})

    def _ensure_conversation(self, conversation_id: str | None, title: str | None) -> Conversation:
        if conversation_id:
            return self._require_conversation(conversation_id)
        created = self._store.create_conversation(
            title=title or "新对话",
            model=self._runtime.models.default,
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
        tool_result: object | None = None
        if message.tool_calls:
            try:
                tool_calls = json.loads(message.tool_calls)
            except json.JSONDecodeError:
                tool_calls = message.tool_calls
        if message.tool_result:
            try:
                tool_result = json.loads(message.tool_result)
            except json.JSONDecodeError:
                tool_result = message.tool_result
        return {
            "id": message.id,
            "conversation_id": message.conversation_id,
            "role": message.role,
            "content": message.content,
            "thinking": message.thinking,
            "tool_name": message.tool_name,
            "tool_calls": tool_calls,
            "tool_result": tool_result,
            "response_to_message_id": message.response_to_message_id,
            "version_number": message.version_number,
            "active": message.active,
            "created_at": message.created_at,
        }

    @staticmethod
    def _parse_tool_result(message: Message) -> object | None:
        if not message.tool_result:
            return None
        try:
            return json.loads(message.tool_result)
        except json.JSONDecodeError:
            return message.tool_result

    @staticmethod
    def _tool_message_parts(message: Message) -> tuple[str, str, str]:
        normalized = re.sub(r"^\[(ok|error)\]\s*", "", message.content or "").strip()
        newline_index = normalized.find("\n")
        if newline_index < 0:
            headline = normalized
            detail = normalized
        else:
            headline = normalized[:newline_index].strip()
            detail = normalized[newline_index + 1 :].strip() or headline
        status = "成功" if (message.content or "").startswith("[ok]") else "失败" if (message.content or "").startswith("[error]") else "完成"
        return status, headline, detail

    def _message_record(self, message: Message, *, version_count: int = 1) -> MessageRecord:
        tool_calls: list[dict] = []
        tool_result: Any | None = None
        if message.tool_calls:
            try:
                tool_calls = json.loads(message.tool_calls)
            except json.JSONDecodeError:
                tool_calls = []
        if message.tool_result:
            try:
                tool_result = json.loads(message.tool_result)
            except json.JSONDecodeError:
                tool_result = message.tool_result
        return MessageRecord(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role,
            model=json.loads(message.metadata or "{}").get("model", ""),
            params=json.loads(message.metadata or "{}").get("params", {}),
            status=json.loads(message.metadata or "{}").get("status", ""),
            content=message.content,
            thinking=message.thinking,
            tool_calls=tool_calls,
            tool_name=message.tool_name,
            tool_result=tool_result,
            response_to_message_id=message.response_to_message_id,
            version_number=message.version_number,
            version_count=version_count,
            active=message.active,
            created_at=message.created_at,
        )

    def _response_version_counts(self, conversation_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for message in self._store.get_messages(conversation_id, include_inactive=True):
            if message.role != "assistant" or not message.response_to_message_id:
                continue
            counts[message.response_to_message_id] = max(counts.get(message.response_to_message_id, 0), message.version_number)
        return counts

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

    async def _call_skill_files(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
    ) -> dict:
        url = f"{config.SKILL_FILES_URL.rstrip('/')}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(method, url, json=json_body)

        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text or f"HTTP {response.status_code}"
            raise HTTPException(status_code=response.status_code, detail=str(detail))

        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="skill-files returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="skill-files returned invalid payload")
        return payload

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

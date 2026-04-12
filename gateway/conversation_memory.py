"""
Conversation memory helpers for layered workspace memory.

This module keeps workspace-level memory separate from per-conversation
memory and provides helpers for resolving stable conversation keys from
OpenAI-compatible requests.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("gateway.conv_memory")

SHARED_MEMORY_INDEX = "/workspace/.memory/MEMORY.md"
CONVERSATION_MEMORY_DIR = "/workspace/.memory/conversations"
CONVERSATION_MEMORY_INDEX = f"{CONVERSATION_MEMORY_DIR}/INDEX.md"
ACTIVE_CONVERSATION_LIMIT = int(os.environ.get("MEMORY_ACTIVE_LIMIT", "20"))

_HEADER_ID_FIELDS = (
    "x-chat-id",
    "x-conversation-id",
    "x-session-id",
    "x-openwebui-chat-id",
)
_REQUEST_ID_FIELDS = (
    "conversation_id",
    "chat_id",
    "session_id",
)
_NESTED_ID_FIELDS = (
    "conversation_id",
    "chat_id",
    "session_id",
    "id",
)

_DEFAULT_SHARED_MEMORY = """# Workspace Memory

> 这里记录项目级 / 共享级记忆。用户个人偏好由 Open WebUI Memory 管理，不写入本文件。

## [project] 当前状态
- 暂无已记录的共享项目记忆。
"""

_DEFAULT_CONVERSATION_INDEX = """# Conversation Memory Index

> 当前仅记录对话 key 与预留文件路径；具体对话摘要文件会在后续自动写回能力完成后逐步充实。

| conversation_key | title | status | updated_at | path |
|---|---|---|---|---|
"""


def conversation_memory_path(conversation_key: str) -> str:
    """Return the markdown path reserved for a conversation memory file."""
    return f"{CONVERSATION_MEMORY_DIR}/{conversation_key}.md"


def derive_conversation_title(messages: list[dict[str, Any]]) -> str:
    """Build a compact title from the first user message."""
    for message in messages:
        if message.get("role") != "user":
            continue
        content = _compact_text(message.get("content") or "", limit=72)
        if content:
            return content
    return "未命名对话"


def resolve_conversation_key(
    req: Any,
    messages: list[dict[str, Any]],
    headers: Mapping[str, str] | Any,
    sessions: Any | None = None,
) -> tuple[str, str]:
    """Resolve a stable conversation key.

    Preference order:
    1. Explicit identifiers from request body / metadata / headers
    2. SessionStore fingerprint of early conversation messages
    3. Local fallback fingerprint
    """
    explicit_id = _find_explicit_conversation_id(req, headers)
    if explicit_id:
        return _sanitize_conversation_key(explicit_id), "explicit"

    if sessions and hasattr(sessions, "fingerprint"):
        try:
            return sessions.fingerprint(messages), "fingerprint"
        except Exception as exc:
            logger.debug("SessionStore fingerprint failed: %s", exc)

    return _fallback_fingerprint(messages), "fallback"


async def ensure_memory_scaffold(
    router: Any,
    session_id: str,
    conversation_key: str,
    title: str,
) -> None:
    """Ensure shared and conversation index files exist.

    This intentionally avoids touching per-conversation files on each request,
    so normal chatting does not rewrite `.memory` every turn.
    """
    await _ensure_file(router, session_id, SHARED_MEMORY_INDEX, _DEFAULT_SHARED_MEMORY)
    await _ensure_conversation_index(router, session_id, conversation_key, title)


async def upsert_conversation_record(
    router: Any,
    session_id: str,
    conversation_key: str,
    title: str,
    *,
    status: str = "active",
) -> None:
    """Update conversation index metadata and archive older active entries."""
    current = await _read_text_file(router, session_id, CONVERSATION_MEMORY_INDEX)
    records = _parse_conversation_index(current or _DEFAULT_CONVERSATION_INDEX)
    path = f"`conversations/{conversation_key}.md`"
    records[conversation_key] = {
        "title": (title or "未命名对话").replace("|", "/"),
        "status": status,
        "updated_at": _utc_now(),
        "path": path,
    }
    _apply_archive_limit(records, keep=ACTIVE_CONVERSATION_LIMIT)
    rendered = _render_conversation_index(records)
    if rendered != (current or ""):
        await router.dispatch(
            "file_write",
            {"path": CONVERSATION_MEMORY_INDEX, "content": rendered},
            session_id,
        )


def _find_explicit_conversation_id(
    req: Any,
    headers: Mapping[str, str] | Any,
) -> str | None:
    for field in _REQUEST_ID_FIELDS:
        value = getattr(req, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()

    metadata = getattr(req, "metadata", None)
    if isinstance(metadata, Mapping):
        for field in _NESTED_ID_FIELDS:
            value = metadata.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()

    model_extra = getattr(req, "model_extra", None)
    if isinstance(model_extra, Mapping):
        for field in _REQUEST_ID_FIELDS:
            value = model_extra.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for nested in ("metadata", "chat", "conversation"):
            nested_val = model_extra.get(nested)
            if isinstance(nested_val, Mapping):
                for field in _NESTED_ID_FIELDS:
                    value = nested_val.get(field)
                    if isinstance(value, str) and value.strip():
                        return value.strip()

    header_map = {
        str(key).lower(): value for key, value in dict(headers).items()
    }
    for field in _HEADER_ID_FIELDS:
        value = header_map.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def _sanitize_conversation_key(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw.strip())
    cleaned = cleaned.strip("._-")
    if not cleaned:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return cleaned[:80]


def _fallback_fingerprint(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = message.get("role", "")
        if role in {"system", "tool"}:
            continue
        content = _compact_text(message.get("content") or "", limit=240)
        if content:
            parts.append(f"{role}:{content}")
        if len(parts) >= 6:
            break

    raw = "|".join(parts) if parts else "empty-conversation"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _compact_text(text: str, *, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


async def _ensure_file(
    router: Any,
    session_id: str,
    path: str,
    content: str,
) -> None:
    try:
        await router.dispatch("file_read", {"path": path}, session_id)
    except FileNotFoundError:
        await router.dispatch("file_write", {"path": path, "content": content}, session_id)
    except Exception as exc:
        logger.debug("Cannot ensure %s: %s", path, exc)


async def _ensure_conversation_index(
    router: Any,
    session_id: str,
    conversation_key: str,
    title: str,
) -> None:
    current = await _read_text_file(router, session_id, CONVERSATION_MEMORY_INDEX)
    if current is None:
        current = _DEFAULT_CONVERSATION_INDEX

    records = _parse_conversation_index(current)
    if conversation_key in records:
        return

    records[conversation_key] = {
        "title": (title or "未命名对话").replace("|", "/"),
        "status": "active",
        "updated_at": "-",
        "path": f"`conversations/{conversation_key}.md`",
    }

    await router.dispatch(
        "file_write",
        {"path": CONVERSATION_MEMORY_INDEX, "content": _render_conversation_index(records)},
        session_id,
    )


async def _read_text_file(router: Any, session_id: str, path: str) -> str | None:
    try:
        result = await router.dispatch("file_read", {"path": path}, session_id)
        if isinstance(result, dict):
            return result.get("content", "")
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.debug("Cannot read %s: %s", path, exc)
    return None


def _parse_conversation_index(content: str) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if "conversation_key" in stripped or set(stripped.replace("|", "").strip()) == {"-"}:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) == 3:
            key, title, path = cells
            records[key] = {
                "title": title,
                "status": "active",
                "updated_at": "-",
                "path": path,
            }
        elif len(cells) >= 5:
            key, title, status, updated_at, path = cells[:5]
            records[key] = {
                "title": title,
                "status": status or "active",
                "updated_at": updated_at or "-",
                "path": path,
            }
    return records


def _render_conversation_index(records: dict[str, dict[str, str]]) -> str:
    lines = [
        "# Conversation Memory Index",
        "",
        "> 当前仅记录对话 key、状态与预留文件路径；活跃对话超过上限后会自动归档。",
        "",
        "| conversation_key | title | status | updated_at | path |",
        "|---|---|---|---|---|",
    ]

    ordered = sorted(records.items(), key=lambda item: item[0])
    ordered = sorted(
        ordered,
        key=lambda item: item[1].get("updated_at", ""),
        reverse=True,
    )
    ordered = sorted(
        ordered,
        key=lambda item: item[1].get("status") != "active",
    )

    for key, record in ordered:
        lines.append(
            "| "
            + " | ".join(
                [
                    key,
                    record.get("title", "未命名对话").replace("|", "/"),
                    record.get("status", "active"),
                    record.get("updated_at", "-"),
                    record.get("path", f"`conversations/{key}.md`"),
                ]
            )
            + " |"
        )

    return "\n".join(lines) + "\n"


def _apply_archive_limit(records: dict[str, dict[str, str]], *, keep: int) -> None:
    active_keys = [
        key
        for key, record in records.items()
        if record.get("status", "active") == "active"
    ]
    if len(active_keys) <= keep:
        return

    ranked = sorted(
        active_keys,
        key=lambda key: records[key].get("updated_at", ""),
        reverse=True,
    )
    keep_set = set(ranked[:keep])
    for key in active_keys:
        if key not in keep_set:
            records[key]["status"] = "archived"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

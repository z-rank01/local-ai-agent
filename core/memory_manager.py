"""
Unified memory management — workspace context, conversation memory, writeback.

Merges the former ``conversation_memory``, ``memory_writeback``, and
``workspace_context`` gateway modules into a single class that implements
the ``MemoryHooks`` protocol consumed by ``core.agent.Agent``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .llm_client import LLMClient
    from .tool_router import ToolRouter

from . import config

logger = logging.getLogger("core.memory")

# ── Paths ───────────────────────────────────────────────────────────────────

SHARED_MEMORY_INDEX = config.SHARED_MEMORY_INDEX
CONVERSATION_MEMORY_DIR = config.CONVERSATION_MEMORY_DIR
CONVERSATION_MEMORY_INDEX = config.CONVERSATION_MEMORY_INDEX
ACTIVE_CONVERSATION_LIMIT = config.ACTIVE_CONVERSATION_LIMIT

# ── Size limits ─────────────────────────────────────────────────────────────

_MAX_DIR_CHARS = 1500
_MAX_MEMORY_CHARS = 2000
_MAX_CONVERSATION_CHARS = 1200

# ── Directory overview ──────────────────────────────────────────────────────

_OVERVIEW_DIRS = ["/workspace/data"]

# ── Default content for new memory files ────────────────────────────────────

_DEFAULT_SHARED_MEMORY = """# Workspace Memory

> 这里记录项目级 / 共享级记忆。

## [project] 当前状态
- 暂无已记录的共享项目记忆。
"""

_DEFAULT_CONVERSATION_INDEX = """# Conversation Memory Index

> 当前仅记录对话 key 与预留文件路径；具体对话摘要文件会在后续自动写回能力完成后逐步充实。

| conversation_key | title | status | updated_at | path |
|---|---|---|---|---|
"""

# ── Writeback prompts ──────────────────────────────────────────────────────

_WRITEBACK_SYSTEM = (
    "你是一个记忆整理助手。"
    "你负责从当前对话中提取项目级共享记忆和当前对话的阶段性摘要。"
)

_WRITEBACK_USER = """\
请基于以下内容输出严格 JSON，不要输出解释，不要输出 Markdown 代码块。

目标：
1. 更新当前对话摘要（供后续同一对话恢复时注入）
2. 仅在确有长期价值时，提取一条共享项目记忆

输出 JSON 结构：
{{
  "write_conversation": true,
  "conversation": {{
    "current_goal": "字符串，最多 120 字",
    "confirmed_facts": ["字符串数组，最多 6 条"],
    "current_status": "字符串，最多 120 字",
    "next_step": "字符串，最多 120 字"
  }},
  "shared_memory_entry": "空字符串，或一段以 ## [project] / ## [reference] 开头的 Markdown 记忆条目"
}}

规则：
- 只保留稳定、有复用价值的信息；不要记录临时寒暄。
- 不要记录用户个人偏好、写作风格、语言习惯。
- `shared_memory_entry` 只有在确有长期价值时才填写，否则返回空字符串。
- 所有内容必须来自已给出的上下文，不得编造。

## 当前对话标题
{title}

## 已有对话记忆
{existing_conversation}

## 现有共享记忆索引（节选）
{shared_memory}

## 当前对话转录（节选）
{transcript}
"""


# ── Helpers ─────────────────────────────────────────────────────────────────


def conversation_memory_path(conversation_key: str) -> str:
    return f"{CONVERSATION_MEMORY_DIR}/{conversation_key}.md"


def _compact_text(text: str, *, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_line(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:160]


def _parse_json_response(raw: str) -> dict[str, Any] | None:
    text = re.sub(r"<think>[\s\S]*?</think>\s*", "", raw or "").strip()
    text = re.sub(r"^```json\s*|\s*```$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _build_transcript(messages: list[dict[str, Any]], limit: int = 6000) -> str:
    parts: list[str] = []
    for message in messages[-12:]:
        role = message.get("role", "")
        if role == "system":
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "tool" and len(content) > 500:
            content = content[:500] + "…"
        parts.append(f"[{role}] {content}")
    transcript = "\n".join(parts)
    return transcript[-limit:] if len(transcript) > limit else transcript


def _merge_shared_entry(existing: str, entry: str) -> str:
    heading = entry.splitlines()[0].strip() if entry.strip() else ""
    base = existing.strip() if existing else "# Workspace Memory\n"
    if not heading:
        return existing
    if entry in base:
        return existing
    if heading in base:
        pattern = re.compile(rf"{re.escape(heading)}[\s\S]*?(?=\n## \[|\Z)")
        replaced = pattern.sub(entry.strip() + "\n", base, count=1)
        return replaced.rstrip() + "\n"
    if not base.endswith("\n"):
        base += "\n"
    return base + "\n" + entry.strip() + "\n"


def _format_entries(entries: list) -> str:
    lines: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            name = entry.get("name", "?")
            is_dir = entry.get("is_dir") or entry.get("type") == "directory"
            size = entry.get("size")
            if is_dir:
                lines.append(f"  📁 {name}/")
            elif size is not None:
                size_kb = size / 1024
                if size_kb > 1024:
                    lines.append(f"  📄 {name}  ({size_kb/1024:.1f} MB)")
                else:
                    lines.append(f"  📄 {name}  ({size_kb:.0f} KB)")
            else:
                lines.append(f"  📄 {name}")
        elif isinstance(entry, str):
            lines.append(f"  {entry}")
    return "\n".join(lines[:50])


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
    ordered = sorted(ordered, key=lambda item: item[1].get("updated_at", ""), reverse=True)
    ordered = sorted(ordered, key=lambda item: item[1].get("status") != "active")
    for key, record in ordered:
        lines.append(
            "| "
            + " | ".join([
                key,
                record.get("title", "未命名对话").replace("|", "/"),
                record.get("status", "active"),
                record.get("updated_at", "-"),
                record.get("path", f"`conversations/{key}.md`"),
            ])
            + " |"
        )
    return "\n".join(lines) + "\n"


def _apply_archive_limit(records: dict[str, dict[str, str]], *, keep: int) -> None:
    active_keys = [
        key for key, record in records.items()
        if record.get("status", "active") == "active"
    ]
    if len(active_keys) <= keep:
        return
    ranked = sorted(active_keys, key=lambda k: records[k].get("updated_at", ""), reverse=True)
    keep_set = set(ranked[:keep])
    for key in active_keys:
        if key not in keep_set:
            records[key]["status"] = "archived"


# ── MemoryManager ───────────────────────────────────────────────────────────


class MemoryManager:
    """Unified memory management implementing the ``MemoryHooks`` protocol.

    Merges workspace context fetching, conversation memory scaffolding,
    and post-turn memory writeback into a single cohesive class.
    """

    def __init__(
        self,
        router: ToolRouter,
        llm: LLMClient,
        *,
        active_limit: int = ACTIVE_CONVERSATION_LIMIT,
    ) -> None:
        self._router = router
        self._llm = llm
        self._active_limit = active_limit

    # ── MemoryHooks protocol ────────────────────────────────────────────

    def derive_conversation_title(self, messages: list[dict]) -> str:
        """Build a compact title from the first user message."""
        for message in messages:
            if message.get("role") != "user":
                continue
            content = _compact_text(message.get("content") or "", limit=72)
            if content:
                return content
        return "未命名对话"

    async def fetch_workspace_context(
        self, session_id: str, *, conversation_key: str | None = None
    ) -> list[str]:
        """Fetch directory overview + memory index for system prompt injection."""
        sections: list[str] = []

        dir_text = await self._fetch_dir_overview(session_id)
        if dir_text:
            sections.append(dir_text)

        mem_text = await self._fetch_memory_index(session_id)
        if mem_text:
            sections.append(mem_text)

        conv_text = await self._fetch_conversation_memory(session_id, conversation_key)
        if conv_text:
            sections.append(conv_text)

        return sections

    async def ensure_memory_scaffold(
        self, session_id: str, conversation_key: str, title: str
    ) -> None:
        """Ensure shared and conversation index files exist."""
        await self._ensure_file(session_id, SHARED_MEMORY_INDEX, _DEFAULT_SHARED_MEMORY)
        await self._ensure_conversation_index(session_id, conversation_key, title)

    async def update_memory_after_turn(
        self, session_id: str, conversation_key: str, title: str, messages: list[dict]
    ) -> None:
        """Extract and persist conversation/shared memory after a reply."""
        if not messages:
            return

        transcript = _build_transcript(messages)
        if not transcript.strip():
            return

        existing_conversation = await self._read_text(
            session_id, conversation_memory_path(conversation_key)
        )
        shared_memory = await self._read_text(session_id, SHARED_MEMORY_INDEX)
        prompt = _WRITEBACK_USER.format(
            title=title or "未命名对话",
            existing_conversation=(existing_conversation or "无")[:1500],
            shared_memory=(shared_memory or "无")[:2000],
            transcript=transcript,
        )

        try:
            raw = await self._llm.chat(_WRITEBACK_SYSTEM, prompt)
            payload = _parse_json_response(raw)
            if not isinstance(payload, dict):
                return
        except Exception as exc:
            logger.debug("Memory writeback skipped: %s", exc)
            return

        try:
            await self._write_conversation_memory(
                session_id, conversation_key, title, payload,
                existing_conversation or "",
            )
            await self._write_shared_memory(session_id, payload, shared_memory or "")
            await self._upsert_conversation_record(
                session_id, conversation_key, title, status="active"
            )
        except Exception as exc:
            logger.debug("Memory persistence skipped: %s", exc)

    # ── Workspace context internals ─────────────────────────────────────

    async def _fetch_dir_overview(self, session_id: str) -> str | None:
        listings: list[str] = []
        for dir_path in _OVERVIEW_DIRS:
            try:
                result = await self._router.dispatch(
                    "file_list", {"directory": dir_path}, session_id
                )
                if isinstance(result, dict):
                    entries = result.get("entries") or result.get("content") or result.get("files")
                    if isinstance(entries, list):
                        lines = _format_entries(entries)
                        listings.append(f"📂 {dir_path}/\n{lines}")
                    elif isinstance(entries, str):
                        listings.append(f"📂 {dir_path}/\n{entries[:_MAX_DIR_CHARS]}")
            except Exception as exc:
                logger.debug("Cannot list %s: %s", dir_path, exc)

        if not listings:
            return None

        text = "\n".join(listings)
        if len(text) > _MAX_DIR_CHARS:
            text = text[:_MAX_DIR_CHARS] + "\n...(更多文件省略)"
        return f"## Workspace 当前文件概况\n\n{text}"

    async def _fetch_memory_index(self, session_id: str) -> str | None:
        try:
            result = await self._router.dispatch(
                "file_read", {"path": SHARED_MEMORY_INDEX}, session_id
            )
            if isinstance(result, dict):
                content = result.get("content", "")
                if content and not result.get("error"):
                    if len(content) > _MAX_MEMORY_CHARS:
                        content = content[:_MAX_MEMORY_CHARS] + "\n...(记忆索引已截断)"
                    return f"## Workspace 记忆\n\n{content}"
        except FileNotFoundError:
            logger.debug("No memory index at %s", SHARED_MEMORY_INDEX)
        except Exception as exc:
            logger.debug("Cannot read memory index: %s", exc)
        return None

    async def _fetch_conversation_memory(
        self, session_id: str, conversation_key: str | None
    ) -> str | None:
        if not conversation_key:
            return None
        try:
            result = await self._router.dispatch(
                "file_read",
                {"path": conversation_memory_path(conversation_key)},
                session_id,
            )
            if isinstance(result, dict):
                content = result.get("content", "")
                if content and not result.get("error"):
                    if len(content) > _MAX_CONVERSATION_CHARS:
                        content = content[:_MAX_CONVERSATION_CHARS] + "\n...(当前对话记忆已截断)"
                    return f"## 当前对话记忆\n\n{content}"
        except FileNotFoundError:
            logger.debug("No conversation memory for %s", conversation_key)
        except Exception as exc:
            logger.debug("Cannot read conversation memory: %s", exc)
        return None

    # ── Memory scaffold internals ───────────────────────────────────────

    async def _ensure_file(self, session_id: str, path: str, content: str) -> None:
        try:
            await self._router.dispatch("file_read", {"path": path}, session_id)
        except FileNotFoundError:
            await self._router.dispatch(
                "file_write", {"path": path, "content": content}, session_id
            )
        except Exception as exc:
            logger.debug("Cannot ensure %s: %s", path, exc)

    async def _ensure_conversation_index(
        self, session_id: str, conversation_key: str, title: str
    ) -> None:
        current = await self._read_text(session_id, CONVERSATION_MEMORY_INDEX)
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
        await self._router.dispatch(
            "file_write",
            {"path": CONVERSATION_MEMORY_INDEX, "content": _render_conversation_index(records)},
            session_id,
        )

    async def _upsert_conversation_record(
        self, session_id: str, conversation_key: str, title: str, *, status: str = "active"
    ) -> None:
        current = await self._read_text(session_id, CONVERSATION_MEMORY_INDEX)
        records = _parse_conversation_index(current or _DEFAULT_CONVERSATION_INDEX)
        path = f"`conversations/{conversation_key}.md`"
        records[conversation_key] = {
            "title": (title or "未命名对话").replace("|", "/"),
            "status": status,
            "updated_at": _utc_now(),
            "path": path,
        }
        _apply_archive_limit(records, keep=self._active_limit)
        rendered = _render_conversation_index(records)
        if rendered != (current or ""):
            await self._router.dispatch(
                "file_write",
                {"path": CONVERSATION_MEMORY_INDEX, "content": rendered},
                session_id,
            )

    # ── Writeback internals ─────────────────────────────────────────────

    async def _write_conversation_memory(
        self,
        session_id: str,
        conversation_key: str,
        title: str,
        payload: dict[str, Any],
        existing: str,
    ) -> None:
        if not payload.get("write_conversation", True):
            return

        conversation = payload.get("conversation")
        if not isinstance(conversation, dict):
            return

        facts = [
            _clean_line(item)
            for item in conversation.get("confirmed_facts", [])
            if isinstance(item, str) and _clean_line(item)
        ][:6]
        current_goal = _clean_line(conversation.get("current_goal", ""))
        current_status = _clean_line(conversation.get("current_status", ""))
        next_step = _clean_line(conversation.get("next_step", ""))
        if not any([current_goal, facts, current_status, next_step]):
            return

        lines = [
            "# 对话记忆",
            "",
            f"- conversation_key: `{conversation_key}`",
            f"- title: {title or '未命名对话'}",
            "",
            "## 当前目标",
            current_goal or "无",
            "",
            "## 已确认事实",
        ]
        if facts:
            lines.extend(f"- {fact}" for fact in facts)
        else:
            lines.append("- 无")
        lines.extend([
            "",
            "## 当前状态",
            current_status or "无",
            "",
            "## 下一步",
            next_step or "无",
            "",
        ])
        rendered = "\n".join(lines)
        if rendered == existing:
            return

        await self._router.dispatch(
            "file_write",
            {"path": conversation_memory_path(conversation_key), "content": rendered},
            session_id,
        )

    async def _write_shared_memory(
        self, session_id: str, payload: dict[str, Any], existing: str
    ) -> None:
        entry = str(payload.get("shared_memory_entry", "") or "").strip()
        if not entry:
            return
        if not entry.startswith("## [project]") and not entry.startswith("## [reference]"):
            return

        updated = _merge_shared_entry(existing, entry)
        if updated == existing:
            return

        await self._router.dispatch(
            "file_write",
            {"path": SHARED_MEMORY_INDEX, "content": updated},
            session_id,
        )

    # ── IO helpers ──────────────────────────────────────────────────────

    async def _read_text(self, session_id: str, path: str) -> str | None:
        try:
            result = await self._router.dispatch("file_read", {"path": path}, session_id)
            if isinstance(result, dict):
                return result.get("content", "")
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.debug("Cannot read %s: %s", path, exc)
        return None

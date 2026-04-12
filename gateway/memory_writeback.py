"""
Post-turn memory writeback for workspace and conversation memory.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from conversation_memory import (
    SHARED_MEMORY_INDEX,
    conversation_memory_path,
    upsert_conversation_record,
)

logger = logging.getLogger("gateway.memory_writeback")

_WRITEBACK_SYSTEM = (
    "你是一个记忆整理助手。"
    "你负责从当前对话中提取项目级共享记忆和当前对话的阶段性摘要。"
    "禁止记录用户个人偏好、语气偏好、输出格式偏好，这些属于 Open WebUI Memory。"
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


async def update_memory_after_turn(
    router: Any,
    llm: Any,
    session_id: str,
    conversation_key: str,
    title: str,
    messages: list[dict[str, Any]],
) -> None:
    """Extract and persist conversation/shared memory after a reply."""
    if not messages:
        return

    transcript = _build_transcript(messages)
    if not transcript.strip():
        return

    existing_conversation = await _read_text(
        router, session_id, conversation_memory_path(conversation_key)
    )
    shared_memory = await _read_text(router, session_id, SHARED_MEMORY_INDEX)
    prompt = _WRITEBACK_USER.format(
        title=title or "未命名对话",
        existing_conversation=(existing_conversation or "无")[:1500],
        shared_memory=(shared_memory or "无")[:2000],
        transcript=transcript,
    )

    try:
        raw = await llm.chat(_WRITEBACK_SYSTEM, prompt)
        payload = _parse_json_response(raw)
        if not isinstance(payload, dict):
            return
    except Exception as exc:
        logger.debug("Memory writeback skipped: %s", exc)
        return

    try:
        await _write_conversation_memory(
            router,
            session_id,
            conversation_key,
            title,
            payload,
            existing_conversation or "",
        )
        await _write_shared_memory(
            router,
            session_id,
            payload,
            shared_memory or "",
        )
        await upsert_conversation_record(
            router,
            session_id,
            conversation_key,
            title,
            status="active",
        )
    except Exception as exc:
        logger.debug("Memory persistence skipped: %s", exc)


async def _write_conversation_memory(
    router: Any,
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
    lines.extend(
        [
            "",
            "## 当前状态",
            current_status or "无",
            "",
            "## 下一步",
            next_step or "无",
            "",
        ]
    )
    rendered = "\n".join(lines)
    if rendered == existing:
        return

    await router.dispatch(
        "file_write",
        {"path": conversation_memory_path(conversation_key), "content": rendered},
        session_id,
    )


async def _write_shared_memory(
    router: Any,
    session_id: str,
    payload: dict[str, Any],
    existing: str,
) -> None:
    entry = str(payload.get("shared_memory_entry", "") or "").strip()
    if not entry:
        return
    if not entry.startswith("## [project]") and not entry.startswith("## [reference]"):
        return

    updated = _merge_shared_entry(existing, entry)
    if updated == existing:
        return

    await router.dispatch(
        "file_write",
        {"path": SHARED_MEMORY_INDEX, "content": updated},
        session_id,
    )


async def _read_text(router: Any, session_id: str, path: str) -> str | None:
    try:
        result = await router.dispatch("file_read", {"path": path}, session_id)
        if isinstance(result, dict):
            return result.get("content", "")
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.debug("Cannot read memory file %s: %s", path, exc)
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
    if len(transcript) <= limit:
        return transcript
    return transcript[-limit:]


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


def _merge_shared_entry(existing: str, entry: str) -> str:
    heading = entry.splitlines()[0].strip() if entry.strip() else ""
    base = existing.strip() if existing else "# Workspace Memory\n"
    if not heading:
        return existing
    if entry in base:
        return existing
    if heading in base:
        pattern = re.compile(
            rf"{re.escape(heading)}[\s\S]*?(?=\n## \[|\Z)"
        )
        replaced = pattern.sub(entry.strip() + "\n", base, count=1)
        return replaced.rstrip() + "\n"
    if not base.endswith("\n"):
        base += "\n"
    return base + "\n" + entry.strip() + "\n"


def _clean_line(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:160]

"""
Context Manager — manages context window efficiency for small LLMs.

Implements:
- Token estimation (character-based, suitable for LLaMA/Qwen tokenizers)
- MicroCompact: replaces old tool result contents with compact markers
- AutoCompact: LLM-based conversation summarization when MicroCompact isn't enough
- Tool result truncation utilities
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from llm_client import LLMClient

logger = logging.getLogger("gateway.context")

# Conservative estimates for mixed Chinese/English text
_AVG_CHARS_PER_TOKEN = 2.5
_PADDING_FACTOR = 1.33

_COMPACT_MARKER = "[已清除旧工具输出，概要: {preview}]"
_TRUNCATE_MARKER = "\n...[已截断，仅显示前 {n} 字符，共 {total} 字符]"

# ── AutoCompact prompts ──────────────────────────────────────────────────────

_AUTOCOMPACT_SYSTEM = (
    "你是一个对话摘要助手。将对话历史压缩为结构化摘要，保留所有关键信息。"
)

_AUTOCOMPACT_USER = """\
请将以下对话历史压缩为结构化摘要。严格按以下 5 段输出，每段不超过 200 字：

## 用户意图
用户的核心需求和目标

## 关键数据
对话中出现的重要数字、文件名、路径、代码片段

## 已完成步骤
AI 已经执行的操作和关键结果

## 当前状态
最新的进展或等待中的任务

## 下一步
用户或 AI 接下来需要做什么

规则：保留所有数字、文件名、路径的原文。不要编造未提及的信息。某段无内容写"无"。

---
对话历史：
{conversation}"""


class ContextManager:
    """Manages context window efficiency for small LLMs.

    Applies MicroCompact (clearing old tool results) when the estimated
    token count exceeds a configurable threshold of the context window.
    When MicroCompact is not enough, AutoCompact summarizes older messages
    via an LLM call.
    """

    def __init__(
        self,
        context_window: int = 32768,
        compact_threshold: float = 0.6,
        preserve_recent: int = 6,
        llm: LLMClient | None = None,
    ):
        self._context_window = context_window
        self._compact_threshold = compact_threshold
        self._preserve_recent = preserve_recent
        self._llm = llm

    @property
    def context_window(self) -> int:
        return self._context_window

    # ── Token estimation ─────────────────────────────────────────────────

    def estimate_tokens(self, messages: list[dict]) -> int:
        """Rough token estimation for a message list.

        Uses a character-based heuristic (~2.5 chars/token average for
        mixed Chinese/English text) with conservative padding.
        """
        total_chars = 0
        for m in messages:
            total_chars += len(m.get("content") or "")
            for tc in m.get("tool_calls", []):
                fn = tc.get("function", {})
                args = fn.get("arguments", "")
                total_chars += len(str(args))
                total_chars += len(fn.get("name", ""))
        return int(total_chars / _AVG_CHARS_PER_TOKEN * _PADDING_FACTOR)

    # ── MicroCompact ─────────────────────────────────────────────────────

    def micro_compact(self, messages: list[dict]) -> list[dict]:
        """Replace old tool result contents with compact markers.

        Preserves the most recent ``preserve_recent`` messages untouched.
        Only compacts tool results (role='tool') with content > 200 chars.
        Compacts from oldest to newest until under threshold.
        """
        token_est = self.estimate_tokens(messages)
        threshold = int(self._context_window * self._compact_threshold)

        if token_est <= threshold:
            return messages

        logger.info(
            "MicroCompact triggered: ~%d tokens > threshold %d (%.0f%% of %d window)",
            token_est,
            threshold,
            token_est / self._context_window * 100,
            self._context_window,
        )

        result = list(messages)
        safe_boundary = max(0, len(result) - self._preserve_recent)
        compacted_count = 0

        for i in range(safe_boundary):
            m = result[i]
            if m.get("role") != "tool":
                continue
            content = m.get("content", "")
            if len(content) <= 200:
                continue

            preview = content[:100].replace("\n", " ").strip()
            result[i] = {
                **m,
                "content": _COMPACT_MARKER.format(preview=preview),
            }
            compacted_count += 1

            if self.estimate_tokens(result) <= threshold:
                break

        new_est = self.estimate_tokens(result)
        logger.info(
            "MicroCompact done: %d results cleared, ~%d → %d tokens",
            compacted_count,
            token_est,
            new_est,
        )
        return result

    # ── AutoCompact ──────────────────────────────────────────────────────

    async def auto_compact(self, messages: list[dict]) -> list[dict]:
        """Summarize older messages via LLM when MicroCompact isn't enough.

        Keeps the system prompt and the most recent ``preserve_recent``
        messages, replacing everything in between with a structured summary.
        """
        if not self._llm:
            logger.warning("AutoCompact skipped — no LLM client configured")
            return messages

        # Separate system prompt from conversation
        system_msgs: list[dict] = []
        if messages and messages[0].get("role") == "system":
            system_msgs = [messages[0]]
            work_msgs = messages[1:]
        else:
            work_msgs = messages

        safe_boundary = max(0, len(work_msgs) - self._preserve_recent)
        if safe_boundary < 2:
            return messages

        to_summarize = work_msgs[:safe_boundary]
        to_keep = work_msgs[safe_boundary:]

        # Build conversation text (truncate long tool results)
        conv_parts: list[str] = []
        for m in to_summarize:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if role == "tool" and len(content) > 300:
                content = content[:300] + "…"
            if content:
                conv_parts.append(f"[{role}]: {content}")

        conversation = "\n".join(conv_parts)
        prompt = _AUTOCOMPACT_USER.format(conversation=conversation)

        token_est_before = self.estimate_tokens(messages)
        logger.info(
            "AutoCompact triggered: ~%d tokens, summarizing %d messages",
            token_est_before,
            len(to_summarize),
        )

        try:
            summary = await self._llm.chat(_AUTOCOMPACT_SYSTEM, prompt)
            # Strip thinking tags if the model included them
            summary = re.sub(r"<think>[\s\S]*?</think>\s*", "", summary).strip()
        except Exception as exc:
            logger.error("AutoCompact LLM call failed: %s", exc)
            return messages

        if not summary:
            logger.warning("AutoCompact returned empty summary, keeping original")
            return messages

        summary_msg: dict = {
            "role": "user",
            "content": (
                "[系统对话摘要 — 以下是之前对话的结构化总结，请基于此继续]\n\n"
                + summary
            ),
        }

        result = system_msgs + [summary_msg] + to_keep
        token_est_after = self.estimate_tokens(result)
        logger.info(
            "AutoCompact done: %d → %d messages, ~%d → %d tokens (saved %.0f%%)",
            len(messages),
            len(result),
            token_est_before,
            token_est_after,
            (1 - token_est_after / max(token_est_before, 1)) * 100,
        )
        return result

    # ── Main entry ───────────────────────────────────────────────────────

    async def process(self, messages: list[dict]) -> list[dict]:
        """Apply all context optimizations to a message list.

        1. MicroCompact — clear old tool results (fast, no LLM call)
        2. AutoCompact — summarize via LLM if still over threshold
        """
        result = self.micro_compact(messages)

        threshold = int(self._context_window * self._compact_threshold)
        if self.estimate_tokens(result) > threshold:
            result = await self.auto_compact(result)

        return result

    # ── Truncation utilities ─────────────────────────────────────────────

    @staticmethod
    def truncate_result(content: str, max_chars: int) -> str:
        """Truncate a string to *max_chars* with a descriptive marker."""
        if len(content) <= max_chars:
            return content
        return content[:max_chars] + _TRUNCATE_MARKER.format(
            n=max_chars, total=len(content)
        )

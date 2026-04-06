"""
Context Manager — manages context window efficiency for small LLMs.

Implements:
- Token estimation (character-based, suitable for LLaMA/Qwen tokenizers)
- MicroCompact: replaces old tool result contents with compact markers
- Tool result truncation utilities
"""

import json
import logging
from typing import Any

logger = logging.getLogger("gateway.context")

# Conservative estimates for mixed Chinese/English text
_AVG_CHARS_PER_TOKEN = 2.5
_PADDING_FACTOR = 1.33

_COMPACT_MARKER = "[已清除旧工具输出，概要: {preview}]"
_TRUNCATE_MARKER = "\n...[已截断，仅显示前 {n} 字符，共 {total} 字符]"


class ContextManager:
    """Manages context window efficiency for small LLMs.

    Applies MicroCompact (clearing old tool results) when the estimated
    token count exceeds a configurable threshold of the context window.
    """

    def __init__(
        self,
        context_window: int = 32768,
        compact_threshold: float = 0.6,
        preserve_recent: int = 6,
    ):
        self._context_window = context_window
        self._compact_threshold = compact_threshold
        self._preserve_recent = preserve_recent

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

    # ── Main entry ───────────────────────────────────────────────────────

    def process(self, messages: list[dict]) -> list[dict]:
        """Apply all context optimizations to a message list."""
        return self.micro_compact(messages)

    # ── Truncation utilities ─────────────────────────────────────────────

    @staticmethod
    def truncate_result(content: str, max_chars: int) -> str:
        """Truncate a string to *max_chars* with a descriptive marker."""
        if len(content) <= max_chars:
            return content
        return content[:max_chars] + _TRUNCATE_MARKER.format(
            n=max_chars, total=len(content)
        )

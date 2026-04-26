"""
Agent — framework-agnostic agentic loop with tool calling.

This module extracts the core agent logic from the former FastAPI gateway
into a reusable class that yields structured events.  Any frontend (TUI,
SSE endpoint, etc.) can consume ``AgentEvent`` objects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Protocol, runtime_checkable

from .audit_logger import AuditLogger
from .context_manager import ContextManager
from .llm_client import LLMClient, strip_think_tags_from_history
from .prompt_builder import PromptBuilder
from .tool_registry import ToolRegistry
from .tool_router import ToolRouter

logger = logging.getLogger("core.agent")

# Per-tool call budgets to prevent search loops
_TOOL_BUDGETS: dict[str, int] = {"web_search": 2, "web_fetch": 2}


# ── Events ──────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class AgentEvent:
    """Structured event emitted by the agent loop.

    Kinds
    -----
    token        Incremental text token for display.
    tool_start   A tool is about to be called.  ``data`` has ``name``, ``params``.
    tool_end     A tool call finished.  ``data`` has ``name``, ``status``, ``elapsed``.
    done         Agent loop completed.  ``text`` has the full final reply.
    error        An unrecoverable error occurred.  ``text`` has details.
    """

    kind: str
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ── Memory hooks protocol ──────────────────────────────────────────────────


@runtime_checkable
class MemoryHooks(Protocol):
    """Optional memory subsystem injected into the Agent.

    Phase 1.4 will provide a concrete ``MemoryManager`` implementation.
    """

    async def fetch_workspace_context(
        self, session_id: str, *, conversation_key: str | None = None
    ) -> list[str]: ...

    async def ensure_memory_scaffold(
        self, session_id: str, conversation_key: str, title: str
    ) -> None: ...

    async def update_memory_after_turn(
        self, session_id: str, conversation_key: str, title: str, messages: list[dict]
    ) -> None: ...

    def derive_conversation_title(self, messages: list[dict]) -> str: ...


# ── Utilities ───────────────────────────────────────────────────────────────


def _format_tool_params(tool_name: str, params: dict) -> str:
    """Format tool parameters into a brief human-readable string."""
    if not params:
        return ""
    parts: list[str] = []
    for key, val in params.items():
        val_str = str(val)
        if len(val_str) > 60:
            val_str = val_str[:57] + "..."
        if key in ("code", "content"):
            parts.append(f"{key}: ({len(str(val))}字符)")
        elif key == "packages" and isinstance(val, list):
            parts.append(f"packages: [{', '.join(val)}]")
        else:
            parts.append(f"{key}: {val_str}")
    return "→ " + ", ".join(parts)


def _parse_tool_args(raw_args: Any) -> dict:
    """Parse tool call arguments, handling both dict and JSON string."""
    if isinstance(raw_args, str):
        try:
            return json.loads(raw_args)
        except json.JSONDecodeError:
            return {}
    return raw_args if isinstance(raw_args, dict) else {}


def _format_prefetch_content(fname: str, content: str, max_chars: int = 10_000) -> str:
    text = content[:max_chars] + ("\n...[截断]" if len(content) > max_chars else "")
    return f"【文件: {fname}】\n{text}"


def _format_tool_result_preview(result: Any, max_chars: int = 1600) -> str:
    try:
        text = json.dumps(result, ensure_ascii=False, default=str, indent=2)
    except Exception:
        text = str(result)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[已截断，仅显示前 {max_chars} 字符，共 {len(text)} 字符]"


# ── Agent ───────────────────────────────────────────────────────────────────


class Agent:
    """Framework-agnostic agentic loop with streaming tool calling.

    Parameters
    ----------
    llm : LLMClient
    router : ToolRouter
    registry : ToolRegistry
    audit : AuditLogger
    context_mgr : ContextManager
    prompt_builder : PromptBuilder
    memory : MemoryHooks | None
        Optional memory subsystem (Phase 1.4).
    tool_tier : str
        ``"core"`` or ``"all"``.
    max_rounds : int
        Maximum tool-call rounds per request.
    """

    def __init__(
        self,
        *,
        llm: LLMClient,
        router: ToolRouter,
        registry: ToolRegistry,
        audit: AuditLogger,
        context_mgr: ContextManager,
        prompt_builder: PromptBuilder,
        memory: MemoryHooks | None = None,
        tool_tier: str = "core",
        max_rounds: int = 6,
    ) -> None:
        self.llm = llm
        self.router = router
        self.registry = registry
        self.audit = audit
        self.context_mgr = context_mgr
        self.prompt_builder = prompt_builder
        self.memory = memory
        self.tool_tier = tool_tier
        self.max_rounds = max_rounds

    # ── Public API ──────────────────────────────────────────────────────

    async def run(
        self,
        messages: list[dict],
        session_id: str = "default",
        conversation_key: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run the full agentic loop, yielding ``AgentEvent`` objects.

        This is the primary streaming interface.  The caller should iterate
        over the generator and render each event.
        """
        conversation_key = conversation_key or session_id
        messages = strip_think_tags_from_history(messages)

        # Memory scaffold
        if self.memory:
            title = self.memory.derive_conversation_title(messages)
            try:
                await self.memory.ensure_memory_scaffold(
                    session_id, conversation_key, title
                )
            except Exception as exc:
                logger.debug("Memory scaffold failed: %s", exc)

        # System prompt with workspace context
        ws_sections: list[str] = []
        if self.memory:
            try:
                ws_sections = await self.memory.fetch_workspace_context(
                    session_id, conversation_key=conversation_key
                )
            except Exception as exc:
                logger.debug("Workspace context fetch failed: %s", exc)

        if not messages or messages[0].get("role") != "system":
            messages.insert(0, {
                "role": "system",
                "content": self.prompt_builder.build(extra_sections=ws_sections),
            })

        # Pre-fetch file context & compact
        messages = await self._inject_context_into_messages(messages, session_id)
        messages = await self.context_mgr.process(messages)

        # Tool definitions
        use_short = self.tool_tier == "core"
        tool_defs: list[dict] | None = self.registry.get_definitions(
            tier=self.tool_tier, use_short_desc=use_short
        )
        if not self.llm._supports_tools:
            tool_defs = None

        tool_call_counts: dict[str, int] = {}
        final_reply = ""

        try:
            for _round in range(self.max_rounds):
                accumulated_msg: dict | None = None

                # Remove over-budget tools
                active_defs = tool_defs
                if tool_defs and tool_call_counts:
                    exhausted = {
                        name
                        for name, limit in _TOOL_BUDGETS.items()
                        if tool_call_counts.get(name, 0) >= limit
                    }
                    if exhausted:
                        active_defs = [
                            d
                            for d in tool_defs
                            if d.get("function", {}).get("name") not in exhausted
                        ]
                        logger.info("Round %d: removed exhausted tools %s", _round, exhausted)

                # Stream tokens from LLM
                async for token, msg in self.llm.chat_stream_with_tools(
                    messages, active_defs
                ):
                    if msg is not None:
                        accumulated_msg = msg
                    elif token:
                        yield AgentEvent("token", text=token)

                if accumulated_msg is None:
                    break

                tool_calls = accumulated_msg.get("tool_calls")
                if not tool_calls:
                    final_reply = accumulated_msg.get("content") or ""
                    if final_reply:
                        messages.append({"role": "assistant", "content": final_reply})
                    break

                # Budget filtering
                filtered_calls = self._filter_tool_calls(tool_calls, tool_call_counts)

                if not filtered_calls:
                    messages.append(accumulated_msg)
                    messages.append({
                        "role": "tool",
                        "content": json.dumps(
                            {"error": "搜索次数已达上限，请直接基于已有搜索结果回答用户问题，不要再搜索。"},
                            ensure_ascii=False,
                        ),
                    })
                    continue

                # Execute filtered tools
                messages.append({**accumulated_msg, "tool_calls": filtered_calls})
                async for event in self._execute_tools(
                    filtered_calls, session_id, messages
                ):
                    yield event

            # Memory writeback (fire-and-forget)
            if final_reply and self.memory:
                title = self.memory.derive_conversation_title(messages)
                asyncio.create_task(
                    self.memory.update_memory_after_turn(
                        session_id, conversation_key, title, messages
                    )
                )

            yield AgentEvent("done", text=final_reply)

        except Exception as exc:
            logger.error("Agent loop error: %s", exc)
            yield AgentEvent("error", text=str(exc))

    async def run_sync(
        self,
        messages: list[dict],
        session_id: str = "default",
        conversation_key: str | None = None,
    ) -> tuple[str, list[dict]]:
        """Non-streaming: run the full loop and return ``(reply, messages)``."""
        final_text = ""
        async for event in self.run(messages, session_id, conversation_key):
            if event.kind == "token":
                final_text += event.text
            elif event.kind == "done":
                if event.text:
                    final_text = event.text
            elif event.kind == "error":
                raise RuntimeError(event.text)
        return final_text, messages

    # ── Internal helpers ────────────────────────────────────────────────

    def _filter_tool_calls(
        self, tool_calls: list[dict], counts: dict[str, int]
    ) -> list[dict]:
        """Apply per-tool budget limits, mutating ``counts`` in place."""
        filtered: list[dict] = []
        for tc in tool_calls:
            fn_name = tc.get("function", {}).get("name", "")
            limit = _TOOL_BUDGETS.get(fn_name)
            if limit is not None and counts.get(fn_name, 0) >= limit:
                logger.info(
                    "Budget exceeded for %s (count=%d, limit=%d), skipping",
                    fn_name, counts.get(fn_name, 0), limit,
                )
                continue
            filtered.append(tc)
            if fn_name in _TOOL_BUDGETS:
                counts[fn_name] = counts.get(fn_name, 0) + 1
        return filtered

    async def _execute_tools(
        self,
        tool_calls: list[dict],
        session_id: str,
        messages: list[dict],
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute tool calls and yield status events."""
        called_names: list[str] = []

        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name: str = fn.get("name", "")
            params = _parse_tool_args(fn.get("arguments", {}))
            params_brief = _format_tool_params(tool_name, params)

            yield AgentEvent(
                "tool_start",
                text=f"`{tool_name}` {params_brief}",
                data={"name": tool_name, "params": params},
            )

            t0 = time.time()
            try:
                result = await self.router.dispatch(tool_name, params, session_id)
                tool_content = json.dumps(result, ensure_ascii=False, default=str)
                result_preview = _format_tool_result_preview(result)
                elapsed = time.time() - t0
                yield AgentEvent(
                    "tool_end",
                    text=f"✅ 成功 ({elapsed:.1f}s)",
                    data={
                        "name": tool_name,
                        "status": "ok",
                        "elapsed": elapsed,
                        "result_preview": result_preview,
                    },
                )
            except (PermissionError, FileNotFoundError, ValueError) as exc:
                tool_content = json.dumps({"error": str(exc)})
                result_preview = str(exc)
                elapsed = time.time() - t0
                yield AgentEvent(
                    "tool_end",
                    text=f"❌ 失败: {exc} ({elapsed:.1f}s)",
                    data={
                        "name": tool_name,
                        "status": "error",
                        "elapsed": elapsed,
                        "error": str(exc),
                        "result_preview": result_preview,
                    },
                )
            except Exception as exc:
                logger.error("Tool %s failed: %s", tool_name, exc)
                err_brief = str(exc)[:200]
                tool_content = json.dumps({"error": err_brief})
                result_preview = err_brief
                elapsed = time.time() - t0
                yield AgentEvent(
                    "tool_end",
                    text=f"❌ 异常: {err_brief} ({elapsed:.1f}s)",
                    data={
                        "name": tool_name,
                        "status": "exception",
                        "elapsed": elapsed,
                        "error": err_brief,
                        "result_preview": result_preview,
                    },
                )

            messages.append({"role": "tool", "content": tool_content})
            called_names.append(tool_name)

        self.audit.record("tool_loop", {"session_id": session_id, "tools_called": called_names})

    async def _inject_context_into_messages(
        self, messages: list[dict], session_id: str
    ) -> list[dict]:
        """Pre-fetch referenced files and append to the last user message."""
        user_content = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        file_context = await self._prefetch_file_context(user_content, session_id)
        if not file_context:
            return messages

        last_user_idx = max(i for i, m in enumerate(messages) if m["role"] == "user")
        augmented = messages[last_user_idx]["content"] + (
            "\n\n---\n[系统已预取以下文件的真实内容，请直接基于这些数据作答，不得虚构任何数字或设备名称]\n\n"
            + file_context
        )
        new_messages = list(messages)
        new_messages[last_user_idx] = {**messages[last_user_idx], "content": augmented}
        return new_messages

    async def _prefetch_file_context(
        self, user_content: str, session_id: str
    ) -> str | None:
        """Detect workspace file references and pre-fetch their contents."""
        _PREFETCH_HINTS = (
            "workspace", "data/", "docs/", "reports/",
            "文件", "文档", "报告", "数据",
        )
        has_hint = any(kw in user_content for kw in _PREFETCH_HINTS)
        has_file_ref = bool(re.search(r"\w+\.\w{2,5}\b", user_content))
        if not has_hint and not has_file_ref:
            return None

        search_dirs = ["/workspace/data", "/workspace/docs", "/workspace/reports"]
        all_entries: list[tuple[str, str]] = []
        for dir_path in search_dirs:
            try:
                listing = await self.router.dispatch(
                    "file_list", {"directory": dir_path}, session_id
                )
                for entry in listing.get("entries", []):
                    if entry["type"] == "file":
                        all_entries.append((dir_path, entry["name"]))
            except Exception:
                continue

        target_files: list[tuple[str, str]] = []
        for dir_path, name in all_entries:
            if name in user_content:
                target_files.append((dir_path, name))
                continue
            for token in re.findall(
                r"['\u2018\u2019\u201c\u201d](.+?)['\u2018\u2019\u201c\u201d]",
                user_content,
            ):
                if name.startswith(token):
                    target_files.append((dir_path, name))
                    break

        if not target_files:
            return None

        parts: list[str] = []
        for dir_path, fname in target_files:
            try:
                result = await self.router.dispatch(
                    "file_read", {"path": f"{dir_path}/{fname}"}, session_id
                )
                if isinstance(result, dict) and result.get("unsupported"):
                    continue
                content: str = result.get("content", "")
                if not content:
                    continue
                formatted = _format_prefetch_content(fname, content)
                parts.append(formatted)
                logger.info("prefetch: injected %s/%s (%d chars)", dir_path, fname, len(formatted))
            except Exception as exc:
                logger.warning("prefetch: could not read %s/%s: %s", dir_path, fname, exc)

        return "\n\n".join(parts) if parts else None

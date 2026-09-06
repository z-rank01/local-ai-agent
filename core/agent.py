"""
Agent — framework-agnostic agentic loop with tool calling.

This module extracts the core agent logic from the former FastAPI gateway
into a reusable class that yields structured events.  Any frontend (TUI,
SSE endpoint, etc.) can consume ``AgentEvent`` objects.
"""

from __future__ import annotations

from contextlib import aclosing
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


def tool_failed(value):
    return isinstance(value, dict) and (bool(value.get('error')) or value.get('success') is False
        or value.get('exit_code', 0) != 0 or value.get('status') == 'failed'
        or tool_failed(value.get('result')))


def model_projection(value):
    if isinstance(value, dict):
        if 'model_observation' in value:
            return model_projection(value['model_observation'])
        return {k: model_projection(v) for k, v in value.items() if k not in ('local_result', 'local_attachments')}
    if isinstance(value, list):
        return [model_projection(v) for v in value]
    return value


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
        self.allow_tools = True

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
        from . import config
        import uuid
        conversation_key = conversation_key or session_id
        messages = strip_think_tags_from_history(messages)
        cloud = getattr(self.llm, "cloud", False)
        try:
            ws_sections = []
            if self.memory and not cloud:
                try:
                    title = self.memory.derive_conversation_title(messages)
                    await self.memory.ensure_memory_scaffold(session_id, conversation_key, title)
                    ws_sections = await self.memory.fetch_workspace_context(session_id, conversation_key=conversation_key)
                except Exception:
                    pass
            if not messages or messages[0].get("role") != "system":
                messages.insert(0, {"role": "system", "content": self.prompt_builder.build(extra_sections=ws_sections)})
            messages = await self.context_mgr.process(messages)
            tool_defs = self.registry.get_definitions(tier=self.tool_tier, use_short_desc=False)
            if cloud and not getattr(self, 'workspace_cloud_allowed', config.WORKSPACE_CLOUD_ALLOWED):
                tool_defs = []
                messages[0]["content"] += "\n当前工作区工具未获云端授权，本轮没有可调用工具。可正常聊天。用户要求读取、列出、分析本地文件或执行代码时，先明确说明尚未执行，并询问是否愿意在顶部“模型设置”开启“允许云端使用当前工作区工具”（文件内容和工具结果可能发送到云端模型）。用户也可选择本地模型。不要声称正在查看或执行，不要编造文件内容，不要以未执行的代码代替任务完成；仅在用户要求代码示例时提供并标明未执行。聊天中的同意不能代替设置开关，必须由用户在界面操作。"
            if not self.allow_tools:
                tool_defs = []
                messages[0]['content'] += '\n本次仅重新组织回答，使用已有工具结果，不重复执行工具；如需重新执行请用户另发一轮指令。'
            messages[0]['content'] += '\n执行任务必须通过本轮实际提供的工具调用，代码块本身不会执行。没有执行结果时不得声称已经读取、修改或生成文件。用户只要求讨论或只读时遵守该范围。'
            if cloud and not getattr(self.llm, 'spec', {}).get('options', {}).get('enable_thinking', False):
                messages[0]['content'] += '\n当前思考输出关闭。若用户明确要求开启思考模式，请询问是否愿意在顶部“模型设置”开启“思考输出”；不能声称已自行开启。普通任务无需为此打断。'
            counts = {}
            for round_number in range(self.max_rounds):
                active = [d for d in tool_defs if counts.get(d['function']['name'], 0) < _TOOL_BUDGETS.get(d['function']['name'], 1000)]
                if round_number == self.max_rounds - 1:
                    active = []
                    messages.append({"role": "user", "content": "[本轮执行预算已到收尾阶段，请根据已取得的结果回答，明确剩余缺口。]"})
                messages = await self.context_mgr.process(messages)
                accumulated = None
                async with aclosing(self.llm.chat_stream_with_tools(messages, active or None)) as stream:
                    async for token, msg in stream:
                        if msg is not None:
                            accumulated = msg
                        elif token:
                            yield AgentEvent("token", text=token)
                if not accumulated:
                    raise RuntimeError("模型未返回完整消息，已取得的结果仍保留。")
                calls = accumulated.get('tool_calls') or []
                for tc in calls:
                    if not tc.get('id'):
                        tc['id'] = 'call_' + uuid.uuid4().hex
                    tc.setdefault('type', 'function')
                messages.append(accumulated)
                yield AgentEvent('message', data={'message': accumulated})
                if not calls:
                    reply = accumulated.get('content') or ''
                    if not reply:
                        raise RuntimeError('模型返回空回答，已有工具结果仍保留。')
                    if self.memory and not cloud:
                        try:
                            await self.memory.update_memory_after_turn(session_id, conversation_key,
                                self.memory.derive_conversation_title(messages), messages)
                        except Exception:
                            logger.warning('Local memory update failed')
                    yield AgentEvent('done', text=reply)
                    return
                allowed = {d['function']['name'] for d in active}
                async with aclosing(self._execute_tools(calls, session_id, messages, allowed=allowed)) as events:
                    async for event in events:
                        yield event
                for call in calls:
                    name = call['function']['name']
                    counts[name] = counts.get(name, 0) + 1
            yield AgentEvent('error', text='本轮调用已达到上限；已完成的工具结果保留，请继续追问。')
        except Exception as exc:
            logger.error('Agent loop failed: %s', type(exc).__name__)
            yield AgentEvent('error', text=str(exc))

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

    async def _execute_tools(self, tool_calls, session_id, messages, *, allowed=None):
        for tc in tool_calls:
            fn = tc.get('function', {})
            name = fn.get('name', '')
            params = {}
            start = time.monotonic()
            try:
                raw = fn.get('arguments', {})
                params = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(params, dict):
                    raise ValueError('工具参数必须是 JSON 对象')
                if allowed is not None and name not in allowed:
                    raise ValueError('本轮工具未开放或调用额度已用完，请基于已有结果回答。')
                yield AgentEvent('tool_start', data={'name': name, 'params': params, 'call_id': tc['id']})
                result = None
                if hasattr(self.router, 'dispatch_stream'):
                    async with aclosing(self.router.dispatch_stream(name, params, session_id)) as stream:
                        async for packet in stream:
                            if packet.get('event') == 'result':
                                result = packet['result']
                            else:
                                yield AgentEvent('tool_progress', data={'name': name, 'call_id': tc['id'], **packet})
                else:
                    result = await self.router.dispatch(name, params, session_id)
                if result is None:
                    raise RuntimeError('工具未返回最终执行结果')
                failed = tool_failed(result)
                status = 'error' if failed else 'ok'
            except Exception as exc:
                result = {'error': str(exc)}
                status = 'error'
            # Explicit local-only attachments are rendered by the UI, never in model history.
            observation = model_projection(result)
            content = json.dumps(observation, ensure_ascii=False, default=str)
            message = {'role': 'tool', 'tool_call_id': tc['id'], 'tool_name': name, 'content': content}
            messages.append(message)
            yield AgentEvent('message', data={'message': message})
            yield AgentEvent('tool_end', text='已完成' if status == 'ok' else '执行失败', data={
                'name': name, 'call_id': tc['id'], 'params': params, 'status': status,
                'elapsed': time.monotonic() - start, 'result': result, 'result_preview': _format_tool_result_preview(result)})
            self.audit.record('tool_loop', {'session_id': session_id, 'name': name, 'status': status})

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

import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from audit_logger import AuditLogger
from context_manager import ContextManager
from llm_client import LLMClient
from policy_engine import PolicyEngine
from prompt_builder import PromptBuilder
from session_store import SessionStore
from tool_registry import ToolRegistry
from tool_router import ToolRouter

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("gateway")

_POLICY_PATH = "/config/policy.yaml"
_TOOLS_DIR = "/config/tools"
_LOG_PATH = "/logs/audit.jsonl"
_SYSTEM_PROMPT_PATH = "/app/prompts/system.txt"

# Per-tool call budgets to prevent search loops (enforced in both streaming & non-streaming paths)
_TOOL_BUDGETS = {"web_search": 2, "web_fetch": 2}


@asynccontextmanager
async def lifespan(app: FastAPI):
    skill_files_url = os.environ.get("SKILL_FILES_URL", "http://skill-files:8100")
    skill_runner_url = os.environ.get("SKILL_RUNNER_URL", "http://skill-runner:8200")
    skill_websearch_url = os.environ.get("SKILL_WEBSEARCH_URL", "http://skill-websearch:8300")
    ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    enable_websearch = os.environ.get("ENABLE_WEBSEARCH", "false").lower() in ("true", "1", "yes")

    registry = ToolRegistry(_TOOLS_DIR, enable_websearch=enable_websearch)
    policy = PolicyEngine(_POLICY_PATH)
    audit = AuditLogger(_LOG_PATH)
    router = ToolRouter(
        skill_files_url, skill_runner_url, skill_websearch_url,
        policy, audit, registry,
        enable_websearch=enable_websearch,
    )
    llm = LLMClient(ollama_base_url, ollama_model)

    context_window = int(os.environ.get("CONTEXT_WINDOW", "32768"))
    compact_threshold = float(os.environ.get("COMPACT_THRESHOLD", "0.6"))
    context_mgr = ContextManager(
        context_window=context_window,
        compact_threshold=compact_threshold,
        llm=llm,
    )
    sessions = SessionStore()
    prompt = PromptBuilder()

    app.state.registry = registry
    app.state.router = router
    app.state.llm = llm
    app.state.audit = audit
    app.state.context_mgr = context_mgr
    app.state.sessions = sessions
    app.state.prompt = prompt

    logger.info(
        "Gateway started — skill-files=%s skill-runner=%s websearch=%s(enabled=%s) "
        "ollama=%s model=%s tools=%d context_window=%d compact_threshold=%.1f",
        skill_files_url,
        skill_runner_url,
        skill_websearch_url,
        enable_websearch,
        ollama_base_url,
        ollama_model,
        len(registry.known_tools),
        context_window,
        compact_threshold,
    )
    yield

    await router.close()
    await llm.close()


app = FastAPI(title="Agent Gateway", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ToolRequest(BaseModel):
    tool: str
    params: dict = Field(default_factory=dict)
    session_id: str = "default"


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/tool")
async def call_tool(req: ToolRequest, request: Request):
    router: ToolRouter = request.app.state.router
    try:
        result = await router.dispatch(req.tool, req.params, req.session_id)
        return {"ok": True, "result": result}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("Unhandled error in tool=%s", req.tool)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    llm: LLMClient = request.app.state.llm
    audit: AuditLogger = request.app.state.audit
    prompt_builder: PromptBuilder = request.app.state.prompt

    system_prompt = prompt_builder.build()
    reply = await llm.chat(system_prompt, req.message)
    audit.record("chat", {"session_id": req.session_id, "input_length": len(req.message)})
    return {"reply": reply}



# ── OpenAI-compatible request model ─────────────────────────────────────────

class OAIMessage(BaseModel):
    role: str
    content: str | None = None


class OAIChatRequest(BaseModel):
    model: str = ""
    messages: list[OAIMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


# ── Agentic tool-call loop ───────────────────────────────────────────────────

def _load_system_prompt(prompt_builder: PromptBuilder | None = None) -> str:
    if prompt_builder:
        return prompt_builder.build()
    try:
        with open(_SYSTEM_PROMPT_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "You are a helpful local AI assistant with access to a sandboxed file workspace."


def _format_tool_params(tool_name: str, params: dict) -> str:
    """Format tool parameters into a brief human-readable string for display."""
    if not params:
        return ""
    parts = []
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


def _parse_tool_args(raw_args) -> dict:
    """Parse tool call arguments, handling both dict and JSON string formats."""
    if isinstance(raw_args, str):
        try:
            return json.loads(raw_args)
        except json.JSONDecodeError:
            return {}
    return raw_args if isinstance(raw_args, dict) else {}


async def _execute_tools_with_status(
    tool_calls: list[dict],
    router: ToolRouter,
    session_id: str,
    messages: list[dict],
    audit: AuditLogger,
    make_chunk,
):
    """Execute tool calls and yield SSE chunks with visible status for the user.

    This is the central tool execution function used by the SSE streaming path.
    It injects markdown-formatted status lines into the stream so users can see
    which tools are being called, their parameters, and success/failure status.

    Yields SSE chunk strings. Also mutates `messages` in-place (appends tool results).
    """
    called_names: list[str] = []

    yield make_chunk("\n\n<details>\n<summary>🔧 技能调用</summary>\n\n")

    for tc in tool_calls:
        fn = tc.get("function", {})
        tool_name: str = fn.get("name", "")
        params = _parse_tool_args(fn.get("arguments", {}))

        params_brief = _format_tool_params(tool_name, params)
        yield make_chunk(f"- `{tool_name}` {params_brief}")

        t0 = time.time()
        try:
            result = await router.dispatch(tool_name, params, session_id)
            tool_content = json.dumps(result, ensure_ascii=False, default=str)
            elapsed = time.time() - t0
            yield make_chunk(f" — ✅ 成功 ({elapsed:.1f}s)\n")
        except (PermissionError, FileNotFoundError, ValueError) as exc:
            tool_content = json.dumps({"error": str(exc)})
            elapsed = time.time() - t0
            yield make_chunk(f" — ❌ 失败: {exc} ({elapsed:.1f}s)\n")
        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc)
            err_brief = str(exc)[:200]
            tool_content = json.dumps({"error": err_brief})
            elapsed = time.time() - t0
            yield make_chunk(f" — ❌ 异常: {err_brief} ({elapsed:.1f}s)\n")

        messages.append({"role": "tool", "content": tool_content})
        called_names.append(tool_name)

    yield make_chunk("\n</details>\n\n")

    audit.record("tool_loop", {"session_id": session_id, "tools_called": called_names})


def _format_prefetch_content(fname: str, content: str, max_chars: int = 10_000) -> str:
    """Cap prefetched file content and add a header label.

    Format-agnostic: the actual file parsing is handled by skill-files
    (built-in xlsx/pdf) and converter plugins (docx/pptx/etc).
    """
    text = content[:max_chars] + ("\n...[截断]" if len(content) > max_chars else "")
    return f"【文件: {fname}】\n{text}"


async def _prefetch_file_context(
    user_content: str,
    router: ToolRouter,
    session_id: str,
) -> str | None:
    """Detect workspace file references in the user message and pre-fetch their contents.

    This is a reliability guard for small LLMs that sometimes skip tool calls
    and hallucinate file data.  When a data file is referenced, the gateway
    fetches it directly and injects the real content into the conversation so
    the model always works from ground truth.

    Format-agnostic: relies on file_read + auto-chain (file_convert) to handle
    any file type.  No hardcoded extension list — new converter plugins take
    effect automatically.
    """
    _PREFETCH_HINTS = ("workspace", "data/", "docs/", "reports/", "文件", "文档", "报告", "数据")
    has_hint = any(kw in user_content for kw in _PREFETCH_HINTS)
    has_file_ref = bool(re.search(r'\w+\.\w{2,5}\b', user_content))
    if not has_hint and not has_file_ref:
        return None

    # Search across all workspace content directories
    search_dirs = ["/workspace/data", "/workspace/docs", "/workspace/reports"]
    all_entries: list[tuple[str, str]] = []  # (dir_path, entry)
    for dir_path in search_dirs:
        try:
            listing = await router.dispatch("file_list", {"directory": dir_path}, session_id)
            for entry in listing.get("entries", []):
                if entry["type"] == "file":
                    all_entries.append((dir_path, entry["name"]))
        except Exception:
            continue

    # Find which files the user is referring to
    target_files: list[tuple[str, str]] = []  # (dir_path, filename)
    for dir_path, name in all_entries:
        if name in user_content:
            target_files.append((dir_path, name))
            continue
        # Fuzzy pattern: "以'X'开头" or starts-with prefix inside user message
        for token in re.findall(r"['\u2018\u2019\u201c\u201d](.+?)['\u2018\u2019\u201c\u201d]", user_content):
            if name.startswith(token):
                target_files.append((dir_path, name))
                break

    if not target_files:
        return None

    parts: list[str] = []
    for dir_path, fname in target_files:
        try:
            result = await router.dispatch("file_read", {"path": f"{dir_path}/{fname}"}, session_id)
            # Skip files that couldn't be read (unsupported even after auto-chain)
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


async def _run_tool_rounds(
    messages: list[dict],
    router: ToolRouter,
    llm: LLMClient,
    audit: AuditLogger,
    tool_definitions: list[dict],
    session_id: str,
    max_rounds: int = 5,
) -> tuple[bool, str]:
    """Execute all tool-call rounds.

    Returns (any_tools_called, final_text):
      - any_tools_called=False, final_text=<text>  → no tools were used; text already in
                                                      messages[-1] and returned here.
      - any_tools_called=True,  final_text=""       → tools were called; messages ends with
                                                      the last tool result, ready for a
                                                      final LLM call by the caller.
    """
    any_tools_called = False
    tool_call_counts: dict[str, int] = {}

    for _ in range(max_rounds):
        # Remove over-budget tools from definitions for this round
        active_defs = tool_definitions
        if tool_call_counts:
            exhausted = {
                name for name, limit in _TOOL_BUDGETS.items()
                if tool_call_counts.get(name, 0) >= limit
            }
            if exhausted:
                active_defs = [
                    d for d in tool_definitions
                    if d.get("function", {}).get("name") not in exhausted
                ]

        msg = await llm.chat_raw(messages, active_defs)
        tool_calls = msg.get("tool_calls")

        if not tool_calls:
            if not any_tools_called:
                # Simple reply with no tool use — store it and signal caller
                messages.append(msg)
                return False, (msg.get("content") or "")
            else:
                # All tools done; do NOT append — caller will stream the final reply
                return True, ""

        # Per-call budget filtering
        filtered_calls = []
        for tc in tool_calls:
            fn_name = tc.get("function", {}).get("name", "")
            limit = _TOOL_BUDGETS.get(fn_name)
            if limit is not None and tool_call_counts.get(fn_name, 0) >= limit:
                continue
            filtered_calls.append(tc)
            if fn_name in _TOOL_BUDGETS:
                tool_call_counts[fn_name] = tool_call_counts.get(fn_name, 0) + 1

        if not filtered_calls:
            messages.append(msg)
            messages.append({"role": "tool", "content": json.dumps(
                {"error": "搜索次数已达上限，请直接基于已有搜索结果回答用户问题。"},
                ensure_ascii=False)})
            continue

        any_tools_called = True
        messages.append({**msg, "tool_calls": filtered_calls})

        called_names: list[str] = []
        for tc in filtered_calls:
            fn = tc.get("function", {})
            tool_name: str = fn.get("name", "")
            params = _parse_tool_args(fn.get("arguments", {}))

            try:
                result = await router.dispatch(tool_name, params, session_id)
                tool_content = json.dumps(result, ensure_ascii=False, default=str)
            except (PermissionError, FileNotFoundError, ValueError) as exc:
                tool_content = json.dumps({"error": str(exc)})
            except Exception as exc:
                logger.error("Tool %s failed unexpectedly: %s", tool_name, exc)
                tool_content = json.dumps({"error": "tool execution failed"})

            messages.append({"role": "tool", "content": tool_content})
            called_names.append(tool_name)

        audit.record(
            "tool_loop",
            {"session_id": session_id, "tools_called": called_names},
        )

    # Max rounds reached; caller must make a final LLM call
    return True, ""


async def _run_agent_loop(
    messages: list[dict],
    router: ToolRouter,
    llm: LLMClient,
    audit: AuditLogger,
    tool_definitions: list[dict],
    session_id: str = "default",
    context_mgr: ContextManager | None = None,
) -> str:
    """Non-streaming: run all tool calls then return the full final text."""
    messages = await _inject_context_into_messages(messages, router, session_id)
    if context_mgr:
        messages = await context_mgr.process(messages)

    any_tools, text = await _run_tool_rounds(
        messages, router, llm, audit, tool_definitions, session_id
    )
    if not any_tools:
        return text
    # Tools were used — make one final sync call
    final = await llm.chat_raw(messages, None)
    return final.get("content") or ""


async def _inject_context_into_messages(
    messages: list[dict],
    router: ToolRouter,
    session_id: str,
) -> list[dict]:
    """Pre-fetch workspace files mentioned in the last user message and append
    their contents directly to that message so the LLM sees real data inline."""
    user_content = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    file_context = await _prefetch_file_context(user_content, router, session_id)
    if not file_context:
        return messages

    # Append file contents to the last user message
    last_user_idx = max(i for i, m in enumerate(messages) if m["role"] == "user")
    augmented = messages[last_user_idx]["content"] + (
        "\n\n---\n[系统已预取以下文件的真实内容，请直接基于这些数据作答，不得虚构任何数字或设备名称]\n\n"
        + file_context
    )
    new_messages = list(messages)
    new_messages[last_user_idx] = {**messages[last_user_idx], "content": augmented}
    return new_messages


async def _sse_chunks(llm: LLMClient, messages: list[dict], any_tools: bool, cached_text: str):
    """Async generator: yield SSE-ready text chunks for the final reply.

    If tools were called  → true-streaming from Ollama (token by token).
    If no tools were used → the reply was already generated; yield it in small
                            chunks with a short delay for a typing effect.
    """
    if any_tools:
        async for token in llm.chat_stream(messages):
            yield token
    else:
        # Simulate typing: ~4 chars per chunk, 15 ms apart
        chunk_size = 4
        for i in range(0, len(cached_text), chunk_size):
            yield cached_text[i : i + chunk_size]
            await asyncio.sleep(0.015)


# ── OpenAI-compatible endpoints ──────────────────────────────────────────────

@app.get("/v1/models")
async def list_models(request: Request):
    llm: LLMClient = request.app.state.llm
    return {
        "object": "list",
        "data": [
            {
                "id": f"agent:{llm._model}",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "local-agent",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def oai_chat_completions(req: OAIChatRequest, request: Request):
    router: ToolRouter = request.app.state.router
    llm: LLMClient = request.app.state.llm
    audit: AuditLogger = request.app.state.audit
    registry: ToolRegistry = request.app.state.registry
    context_mgr: ContextManager = request.app.state.context_mgr
    prompt_builder: PromptBuilder = request.app.state.prompt

    session_id = req.model or "oai"
    tool_definitions = registry.get_definitions()

    # Build messages; inject system prompt if the caller didn't supply one
    messages: list[dict] = [
        {"role": m.role, "content": m.content or ""} for m in req.messages
    ]
    if not messages or messages[0]["role"] != "system":
        messages.insert(0, {"role": "system", "content": _load_system_prompt(prompt_builder)})

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if req.stream:
        # ── Streaming path ─────────────────────────────────────────────────
        # All blocking work is done INSIDE the SSE generator so that the
        # HTTP response starts immediately and Open WebUI keeps the
        # connection alive while the model is thinking.

        def _make_chunk(
            content: str,
            finish: str | None = None,
            role: str | None = None,
        ) -> str:
            delta: dict = {"content": content}
            if role:
                delta["role"] = role
            obj = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": llm._model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        async def _sse_stream():
            nonlocal messages
            first = True
            try:
                messages = await _inject_context_into_messages(
                    messages, router, session_id
                )
                messages = await context_mgr.process(messages)

                tool_defs = tool_definitions if llm._supports_tools else None
                max_rounds = 6

                tool_call_counts: dict[str, int] = {}

                for _round in range(max_rounds):
                    accumulated_msg = None

                    # Remove over-budget tools from definitions for this round
                    active_defs = tool_defs
                    if tool_defs and tool_call_counts:
                        exhausted = {
                            name for name, limit in _TOOL_BUDGETS.items()
                            if tool_call_counts.get(name, 0) >= limit
                        }
                        if exhausted:
                            active_defs = [
                                d for d in tool_defs
                                if d.get("function", {}).get("name") not in exhausted
                            ]
                            logger.info("Round %d: removed exhausted tools %s", _round, exhausted)

                    # Phase 1: Stream thinking + content tokens from LLM
                    async for token, msg in llm.chat_stream_with_tools(
                        messages, active_defs
                    ):
                        if msg is not None:
                            accumulated_msg = msg
                        elif token:
                            role = "assistant" if first else None
                            yield _make_chunk(token, role=role)
                            first = False

                    if accumulated_msg is None:
                        break

                    tool_calls = accumulated_msg.get("tool_calls")
                    if not tool_calls:
                        break

                    # Per-call budget filtering: prevent multiple searches in one round
                    filtered_calls = []
                    for tc in tool_calls:
                        fn_name = tc.get("function", {}).get("name", "")
                        limit = _TOOL_BUDGETS.get(fn_name)
                        if limit is not None and tool_call_counts.get(fn_name, 0) >= limit:
                            logger.info("Budget exceeded for %s (count=%d, limit=%d), skipping",
                                        fn_name, tool_call_counts.get(fn_name, 0), limit)
                            continue
                        filtered_calls.append(tc)
                        if fn_name in _TOOL_BUDGETS:
                            tool_call_counts[fn_name] = tool_call_counts.get(fn_name, 0) + 1

                    if not filtered_calls:
                        # All calls over budget — tell model to answer with existing results
                        messages.append(accumulated_msg)
                        messages.append({"role": "tool", "content": json.dumps(
                            {"error": "搜索次数已达上限，请直接基于已有搜索结果回答用户问题，不要再搜索。"},
                            ensure_ascii=False)})
                        continue

                    # Phase 2: Execute budget-filtered tools with visible status
                    messages.append({**accumulated_msg, "tool_calls": filtered_calls})
                    async for chunk in _execute_tools_with_status(
                        filtered_calls, router, session_id,
                        messages, audit, _make_chunk,
                    ):
                        if first:
                            first = False
                        yield chunk

                    # Phase 3: Next iteration streams model's answer with tool results

                yield _make_chunk("", finish="stop")
                yield "data: [DONE]\n\n"
            except Exception as exc:
                logger.error("SSE stream error: %s", exc)
                role = "assistant" if first else None
                yield _make_chunk(f"\n[错误: {exc}]", finish="stop", role=role)
                yield "data: [DONE]\n\n"

        return StreamingResponse(_sse_stream(), media_type="text/event-stream")

    # ── Non-streaming path ──────────────────────────────────────────────────
    try:
        reply = await _run_agent_loop(
            messages, router, llm, audit, tool_definitions, session_id,
            context_mgr=context_mgr,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": llm._model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

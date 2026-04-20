import json as _json
import logging
import re as _re
from typing import AsyncGenerator

import httpx

logger = logging.getLogger("core.llm")

_JSON_BLOCK_RE = _re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", _re.DOTALL)

# Known markers that signal the end of model thinking text leaked into content
_THINKING_LEAK_MARKERS = [
    "End thoughts.",
    "End thoughts",
    "end thoughts.",
    "结束思考。",
    "结束思考.",
    "</body>",
    "</response>",
    "Begin response.",
    "begin response.",
    "开始回答。",
    "开始回答.",
]

# Regex for stripping <think>...</think> blocks from message history
_THINK_TAG_RE = _re.compile(r"<think>[\s\S]*?</think>\s*", _re.IGNORECASE)


def strip_think_tags_from_history(messages: list[dict]) -> list[dict]:
    """Strip <think>…</think> blocks from assistant messages in history.

    When the TUI (or any front-end) stores the full assistant response
    (including thinking tags) and sends it back in subsequent requests,
    the model tends to mimic the pattern and leak thinking text into its
    ``content`` output.  Stripping them from history prevents this.
    """
    result: list[dict] = []
    for m in messages:
        if m.get("role") == "assistant" and "<think>" in (m.get("content") or "").lower():
            cleaned = _THINK_TAG_RE.sub("", m["content"]).strip()
            result.append({**m, "content": cleaned})
        else:
            result.append(m)
    return result


def _maybe_extract_tool_calls(msg: dict) -> dict:
    """Normalise tool-call JSON that some models embed in the content field."""
    content: str = (msg.get("content") or "").strip()
    if not content:
        return msg

    fence_match = _JSON_BLOCK_RE.search(content)
    candidate = fence_match.group(1) if fence_match else content

    try:
        parsed = _json.loads(candidate)
    except (_json.JSONDecodeError, ValueError):
        return msg

    calls = parsed if isinstance(parsed, list) else [parsed]
    tool_calls = []
    for call in calls:
        if isinstance(call, dict) and "name" in call:
            tool_calls.append({
                "function": {
                    "name": call["name"],
                    "arguments": call.get("arguments", call.get("parameters", {})),
                }
            })

    if not tool_calls:
        return msg

    logger.debug("Extracted %d tool call(s) from content field", len(tool_calls))
    return {**msg, "tool_calls": tool_calls, "content": ""}


def _wrap_thinking(thinking: str, content: str) -> str:
    """Wrap the thinking process in <think> tags and prepend to content."""
    if not thinking:
        return content
    return f"<think>\n{thinking}\n</think>\n\n{content}"


def _strip_thinking_leaks(text: str) -> str:
    """Remove thinking-process text that leaked into the content field."""
    cleaned = _re.sub(r"<think>[\s\S]*?</think>\s*", "", text)
    for marker in _THINKING_LEAK_MARKERS:
        idx = cleaned.find(marker)
        if idx >= 0 and idx < 1500:
            cleaned = cleaned[idx + len(marker) :].lstrip()
            break
    return cleaned


class _ContentSanitizer:
    """Buffer initial streaming tokens to detect and strip thinking leaks."""

    _MAX_BUFFER = 1500

    def __init__(self) -> None:
        self._buffer = ""
        self._flushed = False

    def feed(self, token: str) -> str:
        if self._flushed:
            return token
        self._buffer += token
        for marker in _THINKING_LEAK_MARKERS:
            idx = self._buffer.find(marker)
            if idx >= 0:
                self._flushed = True
                return self._buffer[idx + len(marker) :].lstrip()
        idx = self._buffer.find("</think>")
        if idx >= 0:
            self._flushed = True
            return self._buffer[idx + 8 :].lstrip()
        if len(self._buffer) >= self._MAX_BUFFER:
            self._flushed = True
            return self._buffer
        return ""

    def flush(self) -> str:
        if not self._flushed and self._buffer:
            self._flushed = True
            return self._buffer
        return ""


class LLMClient:
    def __init__(self, base_url: str, model: str):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._supports_tools: bool = True
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
        )

    @property
    def model(self) -> str:
        return self._model

    async def chat(self, system: str, user_message: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
        }
        try:
            resp = await self._client.post(f"{self._base_url}/api/chat", json=payload)
            resp.raise_for_status()
            msg = resp.json().get("message", {})
            content = msg.get("content", "")
            thinking = msg.get("thinking", "")
            if thinking:
                content = _strip_thinking_leaks(content)
            return _wrap_thinking(thinking, content)
        except httpx.HTTPStatusError as exc:
            logger.error("LLM HTTP error %s: %s", exc.response.status_code, exc)
            raise RuntimeError(
                f"LLM request failed with status {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error("LLM connection error: %s", exc)
            raise RuntimeError(f"Cannot reach LLM at {self._base_url}") from exc

    async def chat_raw(self, messages: list, tools: list | None = None) -> dict:
        """Call Ollama and return the raw message dict (may contain tool_calls)."""
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "stream": False,
        }
        if tools and self._supports_tools:
            payload["tools"] = tools
        try:
            resp = await self._client.post(f"{self._base_url}/api/chat", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400 and "tools" in payload:
                body = exc.response.text or ""
                if "does not support tools" in body:
                    logger.warning(
                        "Model %s does not support tools — disabling for this session",
                        self._model,
                    )
                    self._supports_tools = False
                    payload.pop("tools", None)
                    resp = await self._client.post(
                        f"{self._base_url}/api/chat", json=payload
                    )
                    resp.raise_for_status()
                else:
                    raise RuntimeError(
                        f"LLM request failed with status {exc.response.status_code}"
                    ) from exc
            else:
                logger.error("LLM HTTP error %s: %s", exc.response.status_code, exc)
                raise RuntimeError(
                    f"LLM request failed with status {exc.response.status_code}"
                ) from exc
        except httpx.RequestError as exc:
            logger.error("LLM connection error: %s", exc)
            raise RuntimeError(f"Cannot reach LLM at {self._base_url}") from exc

        msg = resp.json().get("message", {})
        if tools and self._supports_tools and not msg.get("tool_calls"):
            msg = _maybe_extract_tool_calls(msg)
        thinking = msg.get("thinking", "")
        if thinking and not msg.get("tool_calls"):
            cleaned = _strip_thinking_leaks(msg.get("content", ""))
            msg = {**msg, "content": _wrap_thinking(thinking, cleaned)}
        return msg

    async def chat_stream_with_tools(
        self, messages: list, tools: list | None = None
    ) -> AsyncGenerator[tuple[str, dict | None], None]:
        """Stream tokens while detecting tool calls.

        Yields ``(token_text, None)`` for each token, then
        ``("", accumulated_msg)`` as the final yield.
        """
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "stream": True,
        }
        if tools and self._supports_tools:
            payload["tools"] = tools

        accumulated: dict = {"role": "assistant", "content": ""}
        tool_calls_acc: list[dict] = []
        thinking_started = False
        in_thinking = False
        sanitizer: _ContentSanitizer | None = None

        try:
            async with self._client.stream(
                "POST", f"{self._base_url}/api/chat", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue

                    msg = chunk.get("message", {})
                    thinking = msg.get("thinking", "")
                    content = msg.get("content", "")

                    if msg.get("tool_calls"):
                        tool_calls_acc.extend(msg["tool_calls"])

                    if thinking:
                        if not thinking_started:
                            yield ("<think>\n", None)
                            thinking_started = True
                            in_thinking = True
                            sanitizer = _ContentSanitizer()
                        yield (thinking, None)

                    if content:
                        if in_thinking:
                            yield ("\n</think>\n\n", None)
                            in_thinking = False
                        if sanitizer:
                            cleaned = sanitizer.feed(content)
                            if cleaned:
                                accumulated["content"] += cleaned
                                yield (cleaned, None)
                        else:
                            accumulated["content"] += content
                            yield (content, None)

                    if chunk.get("done"):
                        if in_thinking:
                            yield ("\n</think>\n\n", None)
                        if sanitizer:
                            remaining = sanitizer.flush()
                            if remaining:
                                accumulated["content"] += remaining
                                yield (remaining, None)
                        break

            if tool_calls_acc:
                accumulated["tool_calls"] = tool_calls_acc

            yield ("", accumulated)

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400 and tools and self._supports_tools:
                body = getattr(exc.response, "text", "") or ""
                if "does not support tools" in body:
                    logger.warning(
                        "Model %s does not support tools — disabling",
                        self._model,
                    )
                    self._supports_tools = False
                    async for token in self.chat_stream(messages):
                        yield (token, None)
                    yield ("", {"role": "assistant", "content": ""})
                    return
            logger.error("LLM stream error %s", exc.response.status_code)
            yield (f"\n[错误: LLM 请求失败 {exc.response.status_code}]", None)
            yield ("", {"role": "assistant", "content": ""})
        except httpx.RequestError as exc:
            logger.error("LLM stream connection error: %s", exc)
            yield (f"\n[错误: 无法连接 LLM]", None)
            yield ("", {"role": "assistant", "content": ""})

    async def chat_stream(self, messages: list) -> AsyncGenerator[str, None]:
        """Stream a final text response (no tools).

        Yields content tokens. Thinking is wrapped in <think>…</think> tags.
        """
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": True,
        }
        try:
            thinking_started = False
            in_thinking = False
            sanitizer: _ContentSanitizer | None = None
            async with self._client.stream(
                "POST", f"{self._base_url}/api/chat", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = _json.loads(line)
                        msg = chunk.get("message", {})
                        thinking = msg.get("thinking", "")
                        content = msg.get("content", "")

                        if thinking:
                            if not thinking_started:
                                yield "<think>\n"
                                thinking_started = True
                                in_thinking = True
                                sanitizer = _ContentSanitizer()
                            yield thinking

                        if content:
                            if in_thinking:
                                yield "\n</think>\n\n"
                                in_thinking = False
                            if sanitizer:
                                cleaned = sanitizer.feed(content)
                                if cleaned:
                                    yield cleaned
                            else:
                                yield content

                        if chunk.get("done"):
                            if in_thinking:
                                yield "\n</think>\n\n"
                            if sanitizer:
                                remaining = sanitizer.flush()
                                if remaining:
                                    yield remaining
                            break
                    except _json.JSONDecodeError:
                        continue
        except httpx.HTTPStatusError as exc:
            logger.error("LLM stream error %s", exc.response.status_code)
            yield f"\n[错误: LLM 请求失败 {exc.response.status_code}]"
        except httpx.RequestError as exc:
            logger.error("LLM stream connection error: %s", exc)
            yield f"\n[错误: 无法连接 LLM]"

    async def close(self) -> None:
        await self._client.aclose()

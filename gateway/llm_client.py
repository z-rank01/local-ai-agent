import json as _json
import logging
import re as _re
from typing import AsyncGenerator

import httpx

logger = logging.getLogger("gateway.llm")

_JSON_BLOCK_RE = _re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", _re.DOTALL)


def _maybe_extract_tool_calls(msg: dict) -> dict:
    """Normalise tool-call JSON that some models embed in the content field.

    Models like qwen2.5-coder emit something like:
        {"name": "file_read", "arguments": {"path": "..."}}
    as plain text in msg["content"] instead of using msg["tool_calls"].

    This function detects that pattern and rewrites the message so that
    tool_calls is populated and content is cleared, matching the format
    that the gateway's tool-call loop expects.
    """
    content: str = (msg.get("content") or "").strip()
    if not content:
        return msg

    # Strip optional ```json ... ``` fences
    fence_match = _JSON_BLOCK_RE.search(content)
    candidate = fence_match.group(1) if fence_match else content

    try:
        parsed = _json.loads(candidate)
    except (_json.JSONDecodeError, ValueError):
        return msg

    # Normalise a single call or a list of calls
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


class LLMClient:
    def __init__(self, base_url: str, model: str):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._supports_tools: bool = True
        # Reasoning models (e.g. deepseek-r1) may think for a long time;
        # use generous read timeout to avoid premature disconnects.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
        )

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
            # If the model doesn't support tools, retry without them.
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
        # Some models (e.g. qwen2.5-coder) emit tool calls as JSON text in
        # the content field instead of using the tool_calls field.
        # Detect and normalise that pattern here.
        if tools and self._supports_tools and not msg.get("tool_calls"):
            msg = _maybe_extract_tool_calls(msg)
        # For reasoning models (e.g. deepseek-r1), merge the thinking
        # process into content so downstream callers see it.
        thinking = msg.get("thinking", "")
        if thinking and not msg.get("tool_calls"):
            msg = {**msg, "content": _wrap_thinking(thinking, msg.get("content", ""))}
        return msg

    async def chat_stream_with_tools(
        self, messages: list, tools: list | None = None
    ) -> AsyncGenerator[tuple[str, dict | None], None]:
        """Stream tokens while detecting tool calls.

        Yields tuples of ``(token_text, message_or_none)``:

        - ``("text", None)`` for each streamed content / thinking token.
        - ``("", accumulated_msg)`` as the **very last** yield, where
          *accumulated_msg* is the full assistant message dict (may contain
          ``tool_calls``).

        This allows callers to stream tokens to the client in real time while
        still being able to detect and execute tool calls.
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
                        yield (thinking, None)

                    if content:
                        if in_thinking:
                            yield ("\n</think>\n\n", None)
                            in_thinking = False
                        accumulated["content"] += content
                        yield (content, None)

                    if chunk.get("done"):
                        if in_thinking:
                            yield ("\n</think>\n\n", None)
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
                    # Retry without tools via regular stream
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
        """Stream a final text response from Ollama (no tools).

        Yields content tokens.  For reasoning models that produce a separate
        ``thinking`` field, the thinking process is emitted first inside
        ``<think>…</think>`` tags so that front-ends (e.g. Open WebUI) can
        render it in a collapsible block.
        """
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": True,
        }
        try:
            thinking_started = False
            in_thinking = False
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
                            yield thinking

                        if content:
                            if in_thinking:
                                yield "\n</think>\n\n"
                                in_thinking = False
                            yield content

                        if chunk.get("done"):
                            if in_thinking:
                                yield "\n</think>\n\n"
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

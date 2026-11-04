import json as _json
import copy
import logging
import asyncio
import uuid
import re as _re
from typing import AsyncGenerator

import httpx

from . import config

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
        # Ollama `think` field; None means the parameter is not sent at all.
        self.think: bool | None = None
        self._supports_tools: bool = True
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=config.OLLAMA_READ_TIMEOUT, write=30.0, pool=30.0)
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def supports_json_object_output(self) -> bool:
        return getattr(self, 'spec', {}).get('structured_output', {}).get('mode') == 'json_object'

    def with_json_object_output(self):
        if not self.supports_json_object_output:
            raise ValueError('当前冻结模型未确认支持 JSON 输出')
        options = dict(self.spec.get('options', {}))
        if 'format' in options and options['format'] != 'json':
            raise ValueError('显式 format 与 JSON 输出冲突，未覆盖')
        options['format'] = 'json'
        client = copy.copy(self)
        client.spec = {**self.spec, 'options': options}
        return client

    def request_parameters(self) -> dict:
        """Native Ollama parameters, shared by chat and frozen delegated calls."""
        # The harness archives and compacts history. Never let Ollama silently
        # discard the user question or tool evidence to make a request fit.
        parameters = {'truncate': False}
        if self.think is not None:
            parameters['think'] = self.think
        options = getattr(self, 'spec', {}).get('options', {})
        if 'format' in options:
            parameters['format'] = options['format']
        return parameters

    async def chat(self, system: str, user_message: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
        }
        payload.update(self.request_parameters())
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
        payload.update(self.request_parameters())
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

    observes_native_requests = True

    async def chat_stream_with_tools(
        self, messages: list, tools: list | None = None, *, request_observer=None
    ) -> AsyncGenerator[tuple[str, dict | None], None]:
        """Stream tokens while detecting tool calls.

        Yields ``(token_text, None)`` for each token, then
        ``("", accumulated_msg)`` as the final yield.
        """
        normalized = []
        for message in messages:
            item = dict(message)
            item.pop('reasoning_content', None)
            if item.get('tool_calls'):
                item['tool_calls'] = []
                for call in message['tool_calls']:
                    fn = dict(call['function'])
                    if isinstance(fn.get('arguments'), str):
                        fn['arguments'] = _json.loads(fn['arguments'])
                    item['tool_calls'].append(dict(call, function=fn))
            normalized.append(item)
        payload: dict = {
            "model": self._model,
            "messages": normalized,
            "stream": True,
        }
        payload.update(self.request_parameters())
        if tools and self._supports_tools:
            payload["tools"] = tools
        from .native_response import NativeStreamReceipt
        call_id = uuid.uuid4().hex
        receipt = NativeStreamReceipt(call_id)
        if request_observer:
            from .native_tool_schema import tool_schema_observation
            from harness.runtime.request_materials import observe_materials
            request_observer('native_model_request', {
                'call_id': call_id, 'model': 'ollama:' + self._model, 'local_attempt_id': call_id,
                'parameters': {'model': self._model, 'stream': True, **self.request_parameters()},
                'tool_names': [item['function']['name'] for item in tools or []],
                'message_count': len(normalized),
                'input_chars': len(_json.dumps(normalized, ensure_ascii=False)),
                **tool_schema_observation(payload, {}),
                **observe_materials(normalized, {'call_id':call_id, 'local_attempt_id':call_id})})

        accumulated: dict = {"role": "assistant", "content": ""}
        tool_calls_acc: list[dict] = []
        thinking_started = False
        stream_done = False
        in_thinking = False
        sanitizer: _ContentSanitizer | None = None

        try:
            async with self._client.stream(
                "POST", f"{self._base_url}/api/chat", json=payload
            ) as resp:
                receipt.headers(resp.status_code, resp.headers)
                if resp.status_code >= 400:
                    await resp.aread()
                    try:
                        error_body = resp.json()
                    except ValueError:
                        error_body = None
                    receipt.rejection_body(error_body, resp.headers)
                    receipt.failure_category = 'HTTP_REJECTION'
                    receipt.failure_stage = 'http_response'
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = _json.loads(line)
                    except _json.JSONDecodeError:
                        receipt.failure_category = 'STREAM_PROTOCOL_ERROR'
                        raise RuntimeError('本地模型流包含无效 JSON，未忽略或重发。') from None
                    if not isinstance(chunk, dict):
                        receipt.failure_category = 'STREAM_PROTOCOL_ERROR'
                        raise RuntimeError('本地模型流结构无效，未重发。')
                    receipt.activity()
                    if chunk.get('error'):
                        receipt.rejection_body(chunk, resp.headers)
                        receipt.failure_category = 'STREAM_PROVIDER_ERROR'
                        raise RuntimeError('本地模型流返回错误，已保留回执，未重发。')

                    msg = chunk.get("message", {})
                    thinking = msg.get("thinking", "")
                    content = msg.get("content", "")
                    receipt.visible_content_chars += len(content)
                    receipt.reasoning_chars += len(thinking)
                    if chunk.get('done'):
                        reason = chunk.get('done_reason') or 'stop'
                        if reason in ('stop', 'length', 'tool_calls'):
                            receipt.finishes.add(reason)
                        else:
                            receipt.unknown_finish = True
                        usage = {target:chunk[source] for source,target in (
                            ('prompt_eval_count','prompt_tokens'), ('eval_count','completion_tokens'))
                            if type(chunk.get(source)) is int and chunk[source] >= 0}
                        receipt.usage = usage or None
                        receipt.usage_status = 'REPORTED' if usage else 'NOT_REPORTED'

                    if msg.get("tool_calls"):
                        tool_calls_acc.extend(msg["tool_calls"])

                    if thinking:
                        if not thinking_started:
                            yield ("<think>\n", None)
                            thinking_started = True
                            in_thinking = True
                            sanitizer = None
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
                        stream_done = True
                        if in_thinking:
                            yield ("\n</think>\n\n", None)
                        if sanitizer:
                            remaining = sanitizer.flush()
                            if remaining:
                                accumulated["content"] += remaining
                                yield (remaining, None)
                        break

            if not stream_done:
                receipt.failure_category = 'EARLY_EOF'
                raise RuntimeError('本地模型流提前结束，已有内容保留。')
            if receipt.result()['result_status'] != 'COMPLETE':
                receipt.failure_category = 'INCOMPLETE_RESULT'
                raise RuntimeError('本地模型返回未完整结果，已有内容保留，未自动重发。')
            if tool_calls_acc:
                accumulated["tool_calls"] = tool_calls_acc
            if not accumulated['content'] and not tool_calls_acc:
                receipt.failure_category = 'EMPTY_RESULT'
                raise RuntimeError('本地模型没有返回回答或工具调用，未自动重发。')
            receipt.delivery_status = 'COMPLETE'
            yield ("", accumulated)

        except httpx.HTTPStatusError as exc:
            receipt.delivery_status = 'FAILED'
            raise RuntimeError(f'本地模型请求失败（HTTP {exc.response.status_code}），未切换模型或禁用工具。') from None
        except httpx.RequestError as exc:
            receipt.delivery_status = 'FAILED'
            receipt.network_failure(exc)
            raise RuntimeError('本地模型连接失败或超时，已保留收到的内容。') from None
        except (asyncio.CancelledError, GeneratorExit):
            if receipt.delivery_status != 'COMPLETE':
                receipt.delivery_status = 'CANCELLED'
                receipt.failure_category = 'CALLER_CANCELLED'
            raise
        except BaseException:
            receipt.delivery_status = 'FAILED'
            receipt.failure_category = receipt.failure_category or 'STREAM_PROTOCOL_ERROR'
            raise
        finally:
            if request_observer:
                request_observer('native_model_result', {'call_id':call_id,
                    'model':'ollama:' + self._model, **receipt.result()})

    async def chat_stream(self, messages: list) -> AsyncGenerator[str, None]:
        """Stream a final text response (no tools).

        Yields content tokens. Thinking is wrapped in <think>…</think> tags.
        """
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": True,
        }
        payload.update(self.request_parameters())
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
                                sanitizer = None
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

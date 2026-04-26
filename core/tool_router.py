"""
Tool router — dispatches tool calls to skill microservices via HTTP.
"""

import json
import logging
from typing import Any

import httpx

from .audit_logger import AuditLogger
from .conversation_store import ConversationStore
from .policy_engine import PolicyEngine
from .tool_registry import ToolRegistry

logger = logging.getLogger("core.router")


class ToolRouter:
    def __init__(
        self,
        skill_files_url: str,
        skill_runner_url: str,
        skill_websearch_url: str,
        policy: PolicyEngine,
        audit: AuditLogger,
        registry: ToolRegistry,
        store: ConversationStore | None = None,
        *,
        enable_websearch: bool = False,
    ):
        self._backend_urls = {
            "skill-files": skill_files_url.rstrip("/"),
            "skill-runner": skill_runner_url.rstrip("/"),
        }
        if enable_websearch:
            self._backend_urls["skill-websearch"] = skill_websearch_url.rstrip("/")
        self._policy = policy
        self._audit = audit
        self._registry = registry
        self._store = store
        self._client = httpx.AsyncClient(timeout=300.0)

    async def dispatch(self, tool: str, params: dict[str, Any], session_id: str = "default") -> Any:
        if tool not in self._registry.known_tools:
            raise ValueError(f"Unknown tool: {tool!r}")

        self._policy.check(tool, params)

        backend = self._registry.get_backend(tool)
        if backend == "local-runtime":
            result = self._dispatch_local(tool, params)
            self._audit.record(tool, {"session_id": session_id, "params": params, "status": "ok"})
            return result
        base_url = self._backend_urls[backend]

        resp = await self._client.post(
            f"{base_url}/tool/{tool}",
            json=params,
        )

        if resp.status_code == 403:
            raise PermissionError(resp.json().get("detail", "Forbidden"))
        if resp.status_code == 404:
            raise FileNotFoundError(resp.json().get("detail", "Not found"))
        if resp.status_code >= 400:
            try:
                body = resp.json()
                detail = body.get("detail", str(body))
            except Exception:
                detail = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
            raise RuntimeError(
                f"Tool {tool!r} returned HTTP {resp.status_code}: {detail}"
            )

        result = resp.json()

        # Truncate large string values
        max_chars = self._registry.get_max_result_chars(tool)
        if max_chars and isinstance(result, dict):
            for key in ("content", "stdout", "stderr", "output", "text"):
                val = result.get(key)
                if isinstance(val, str) and len(val) > max_chars:
                    result = dict(result)
                    result[key] = (
                        val[:max_chars]
                        + f"\n...[已截断，仅显示前 {max_chars} 字符，共 {len(val)} 字符]"
                    )

        # Auto-chain: file_read → file_convert for unsupported binary files
        if tool == "file_read" and isinstance(result, dict) and result.get("unsupported"):
            if "file_convert" in self._registry.known_tools:
                convert_path = result.get("path") or params.get("path")
                if convert_path:
                    logger.info("Auto-chaining file_read → file_convert for %s", convert_path)
                    try:
                        convert_result = await self._dispatch_convert(convert_path, session_id)
                        if not convert_result.get("unsupported") and not convert_result.get("error"):
                            self._audit.record(
                                "file_convert_auto",
                                {"session_id": session_id, "path": convert_path, "status": "ok"},
                            )
                            return convert_result
                    except Exception as exc:
                        logger.warning("Auto file_convert failed for %s: %s", convert_path, exc)

        self._audit.record(tool, {"session_id": session_id, "params": params, "status": "ok"})
        return result

    def _dispatch_local(self, tool: str, params: dict[str, Any]) -> Any:
        if self._store is None:
            raise RuntimeError(f"Tool {tool!r} requires conversation storage")
        if tool == "conversation_search":
            return self._conversation_search(params)
        if tool == "conversation_read":
            return self._conversation_read(params)
        raise ValueError(f"Unknown local-runtime tool: {tool!r}")

    def _conversation_search(self, params: dict[str, Any]) -> dict[str, Any]:
        query = str(params.get("query", "")).strip()
        if not query:
            raise PermissionError("query must be a non-empty string")

        limit = params.get("limit", 5)
        if not isinstance(limit, int):
            try:
                limit = int(limit)
            except Exception as exc:
                raise PermissionError("limit must be an integer") from exc
        limit = max(1, min(limit, 10))

        conversations = self._store.list_conversations(limit=limit, offset=0, query=query)
        results: list[dict[str, Any]] = []
        lowered = query.casefold()
        for conversation in conversations:
            snippet = ""
            match_role = ""
            match_message_id = ""
            for message in self._store.get_messages(conversation.id):
                if lowered in (message.content or "").casefold():
                    snippet = self._excerpt_match(message.content, query)
                    match_role = message.role
                    match_message_id = message.id
                    break
                if lowered in (message.thinking or "").casefold():
                    snippet = self._excerpt_match(message.thinking, query)
                    match_role = message.role
                    match_message_id = message.id
                    break
            results.append({
                "conversation_id": conversation.id,
                "title": conversation.title,
                "model": conversation.model,
                "updated_at": conversation.updated_at,
                "matched_message_id": match_message_id or None,
                "matched_role": match_role or None,
                "snippet": snippet or conversation.title,
            })

        return {"query": query, "count": len(results), "results": results}

    def _conversation_read(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(params.get("conversation_id", "")).strip()
        if not conversation_id:
            raise PermissionError("conversation_id must be a non-empty string")

        max_messages = params.get("max_messages", 12)
        if not isinstance(max_messages, int):
            try:
                max_messages = int(max_messages)
            except Exception as exc:
                raise PermissionError("max_messages must be an integer") from exc
        max_messages = max(1, min(max_messages, 30))

        conversation = self._store.get_conversation(conversation_id)
        if conversation is None:
            raise FileNotFoundError("conversation not found")

        messages = conversation.messages[-max_messages:]
        return {
            "conversation": {
                "id": conversation.id,
                "title": conversation.title,
                "model": conversation.model,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            },
            "message_count": len(messages),
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "created_at": message.created_at,
                    "content": message.content,
                    "thinking": message.thinking,
                    "tool_name": message.tool_name or None,
                    "tool_calls": self._safe_json_loads(message.tool_calls),
                }
                for message in messages
            ],
        }

    @staticmethod
    def _safe_json_loads(raw: str) -> Any:
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    @staticmethod
    def _excerpt_match(text: str, query: str, radius: int = 90) -> str:
        compact = " ".join(text.split())
        lowered = compact.casefold()
        index = lowered.find(query.casefold())
        if index < 0:
            return compact[: radius * 2] + ("..." if len(compact) > radius * 2 else "")
        start = max(0, index - radius)
        end = min(len(compact), index + len(query) + radius)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(compact) else ""
        return f"{prefix}{compact[start:end]}{suffix}"

    async def _dispatch_convert(self, path: str, session_id: str) -> dict:
        backend = self._registry.get_backend("file_convert")
        base_url = self._backend_urls[backend]
        resp = await self._client.post(
            f"{base_url}/tool/file_convert",
            json={"path": path},
        )
        if resp.status_code >= 400:
            return {"error": f"file_convert HTTP {resp.status_code}"}
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()
